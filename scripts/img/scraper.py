#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import hashlib
import base64
import shutil
from pathlib import Path

import cloudscraper
from bs4 import BeautifulSoup
import cv2
import requests

# ==== 配置 ====
BRIGHTNESS_THRESHOLD = 130
BATCH_SIZE = 100
TEMP_DIR = "temp_download"
LOCAL_DIR = "local_images"

# 起始ID
START_ID = 342
# 最大连续404次数（真正的结束）
MAX_404_COUNT = 5

# 目标私有仓库
TARGET_REPO = os.environ.get("TARGET_REPO", "")
GITHUB_TOKEN = os.environ.get("GH_TOKEN", "")
TARGET_BRANCH = "main"

# 目标仓库中的路径
IMAGES_DIR = "ri"
FOLDERS = ["vd", "vl", "hd", "hl"]

scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
)


# ============ GitHub API ============

def github_get_sha(path: str) -> str | None:
    if not GITHUB_TOKEN or not TARGET_REPO:
        return None
    
    url = f"https://api.github.com/repos/{TARGET_REPO}/contents/{path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.json().get("sha")
    except:
        pass
    return None


def github_get_json(path: str) -> tuple:
    if not GITHUB_TOKEN or not TARGET_REPO:
        return None, None
    
    url = f"https://api.github.com/repos/{TARGET_REPO}/contents/{path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            return content, data["sha"]
    except Exception as e:
        print(f"⚠️ 获取JSON失败 {path}: {e}")
    return None, None


def github_upload(path: str, content: bytes, message: str, sha: str = None) -> bool:
    if not GITHUB_TOKEN or not TARGET_REPO:
        return False
    
    url = f"https://api.github.com/repos/{TARGET_REPO}/contents/{path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    data = {
        "message": message,
        "content": base64.b64encode(content).decode("utf-8"),
        "branch": TARGET_BRANCH
    }
    if sha:
        data["sha"] = sha
    
    try:
        resp = requests.put(url, headers=headers, json=data, timeout=60)
        return resp.status_code in [200, 201]
    except Exception as e:
        print(f"❌ 上传失败 {path}: {e}")
        return False


def get_remote_json(path: str, default=None) -> dict:
    content, _ = github_get_json(path)
    if content:
        try:
            return json.loads(content)
        except:
            pass
    return default if default is not None else {}


def save_remote_json(path: str, data: dict, msg: str) -> bool:
    sha = github_get_sha(path)
    content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    return github_upload(path, content, msg, sha)


def batch_upload_to_github(upload_queue: list, hash_registry: dict, 
                           folder_counts: dict, last_id: int) -> bool:
    """批量上传所有文件到GitHub"""
    if not upload_queue:
        print("📭 没有需要上传的文件")
        return True
    
    print(f"\n{'='*50}")
    print(f"📤 开始批量上传 {len(upload_queue)} 个文件")
    print(f"{'='*50}\n")
    
    success_count = 0
    fail_count = 0
    
    for idx, item in enumerate(upload_queue, 1):
        local_path = item["local_path"]
        remote_path = item["remote_path"]
        file_hash = item["hash"]
        
        print(f"[{idx}/{len(upload_queue)}] {remote_path}", end=" ")
        
        try:
            with open(local_path, "rb") as f:
                content = f.read()
            
            if github_upload(remote_path, content, f"Add {remote_path}"):
                hash_registry[file_hash] = remote_path.replace(f"{IMAGES_DIR}/", "")
                success_count += 1
                print("✅")
            else:
                fail_count += 1
                print("❌")
                folder = remote_path.split("/")[-2]
                if folder in folder_counts:
                    folder_counts[folder] -= 1
        except Exception as e:
            fail_count += 1
            print(f"❌ {e}")
    
    print(f"\n📊 上传完成: 成功 {success_count}, 失败 {fail_count}")
    
    # 上传元数据
    if success_count > 0:
        print("\n📝 更新元数据...")
        
        if save_remote_json(f"{IMAGES_DIR}/hash_registry.json", hash_registry,
                           f"Update hash_registry (+{success_count})"):
            print("  ✅ hash_registry.json")
        
        if save_remote_json(f"{IMAGES_DIR}/count.json", folder_counts, "Update count"):
            print("  ✅ count.json")
        
        # 更新进度
        progress = get_remote_json("progress.json", {"last_id": START_ID - 1})
        progress["last_id"] = last_id
        if save_remote_json("progress.json", progress, f"Update progress to {last_id}"):
            print("  ✅ progress.json")
    
    return fail_count == 0


# ============ 工具函数 ============

def build_url(page_id: int) -> str:
    return f"https://img.hyun.cc/index.php/archives/{page_id}.html"


def get_file_hash(filepath: str) -> str:
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def ensure_dir(path: str):
    Path(path).mkdir(parents=True, exist_ok=True)


# ============ 图片处理 ============

def scrape_images(url: str) -> tuple:
    """
    爬取页面中的图片链接
    返回: (images_list, status)
    status: "ok" | "video" | "404" | "error"
    """
    print(f"🌐 爬取: {url}")
    
    try:
        resp = scraper.get(url, timeout=30)
        
        # 检查404
        if resp.status_code == 404:
            return [], "404"
        
        resp.raise_for_status()
        resp.encoding = 'utf-8'
    except requests.exceptions.HTTPError as e:
        if "404" in str(e):
            return [], "404"
        print(f"❌ 请求失败: {e}")
        return [], "error"
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        return [], "error"
    
    soup = BeautifulSoup(resp.text, "lxml")
    images = []
    
    for idx, link in enumerate(soup.find_all("a", {"data-fancybox": True}), 1):
        href = link.get("href", "")
        if href.startswith("http"):
            images.append({"url": href, "index": idx})
    
    if not images:
        # 没有图片，可能是视频页面
        print(f"🎬 无图片（视频页面），跳过")
        return [], "video"
    
    print(f"📷 找到 {len(images)} 张图片")
    return images, "ok"


def download_image(url: str, save_path: str) -> bool:
    try:
        resp = scraper.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"❌ 下载失败: {e}")
        return False


def convert_to_webp(input_path: str, output_path: str) -> bool:
    try:
        img = cv2.imread(input_path)
        if img is None:
            return False
        cv2.imwrite(output_path, img, [cv2.IMWRITE_WEBP_QUALITY, 85])
        return True
    except:
        return False


def analyze_image(path: str) -> dict | None:
    """分析图片，返回分类文件夹"""
    try:
        img = cv2.imread(path)
        if img is None:
            return None
        
        h, w = img.shape[:2]
        if w < 10 or h < 10:
            return None
        
        orientation = "h" if w >= h else "v"
        
        resized = cv2.resize(img, (100, 100))
        lab = cv2.cvtColor(resized, cv2.COLOR_BGR2LAB)
        avg_l = lab[:, :, 0].mean()
        brightness = "d" if avg_l < BRIGHTNESS_THRESHOLD else "l"
        
        folder = orientation + brightness
        print(f"  📐 {w}x{h} L={avg_l:.1f} → {folder}")
        
        return {"folder": folder}
    except Exception as e:
        print(f"❌ 分析失败: {e}")
        return None


# ============ 本地处理 ============

def process_page_local(page_id: int, hash_registry: dict, folder_counts: dict, 
                       upload_queue: list) -> str:
    """
    本地处理单个页面
    返回: "success" | "video" | "404" | "error"
    """
    url = build_url(page_id)
    
    print(f"\n{'='*50}")
    print(f"📂 页面 ID: {page_id}")
    print(f"{'='*50}")
    
    ensure_dir(TEMP_DIR)
    
    # 爬取图片
    images, status = scrape_images(url)
    
    if status != "ok":
        return status
    
    new_count = 0
    
    for img in images[:BATCH_SIZE]:
        idx = img["index"]
        temp_path = os.path.join(TEMP_DIR, f"temp_{page_id}_{idx}")
        
        print(f"📥 [{idx}/{len(images)}] 下载中...")
        
        if not download_image(img["url"], temp_path):
            continue
        
        # 检查重复
        file_hash = get_file_hash(temp_path)
        if file_hash in hash_registry:
            print(f"  ⏭️ 跳过重复")
            os.remove(temp_path)
            continue
        
        # 分析图片
        info = analyze_image(temp_path)
        if not info:
            os.remove(temp_path)
            continue
        
        # 确定目标路径
        target_folder = info["folder"]
        folder_counts[target_folder] += 1
        new_num = folder_counts[target_folder]
        
        # 本地保存
        local_folder = os.path.join(LOCAL_DIR, IMAGES_DIR, target_folder)
        ensure_dir(local_folder)
        local_path = os.path.join(local_folder, f"{new_num}.webp")
        
        if not convert_to_webp(temp_path, local_path):
            os.remove(temp_path)
            folder_counts[target_folder] -= 1
            continue
        os.remove(temp_path)
        
        # 添加到上传队列
        remote_path = f"{IMAGES_DIR}/{target_folder}/{new_num}.webp"
        upload_queue.append({
            "local_path": local_path,
            "remote_path": remote_path,
            "hash": file_hash
        })
        
        hash_registry[file_hash] = f"{target_folder}/{new_num}.webp"
        new_count += 1
        print(f"  💾 {local_path}")
    
    print(f"✅ 页面 {page_id} 完成，新增 {new_count} 张")
    return "success"


# ============ 主函数 ============

def main():
    print("🚀 开始运行\n")
    
    if not GITHUB_TOKEN:
        print("❌ 缺少 GH_TOKEN")
        return
    if not TARGET_REPO:
        print("❌ 缺少 TARGET_REPO")
        return
    
    print(f"📦 目标仓库: {TARGET_REPO}")
    print(f"📁 存储目录: /{IMAGES_DIR}/\n")
    
    # 获取远程数据
    print("📥 获取远程数据...")
    progress = get_remote_json("progress.json", {"last_id": START_ID - 1})
    hash_registry = get_remote_json(f"{IMAGES_DIR}/hash_registry.json", {})
    raw_folder_counts = get_remote_json(f"{IMAGES_DIR}/count.json", {})
    
    # 兼容旧格式 {"hd": {"max": 123, "exclude": [...]}}
    folder_counts = {}
    for f in FOLDERS:
        val = raw_folder_counts.get(f, 0)
        if isinstance(val, dict):
            folder_counts[f] = val.get("max", 0)
        elif isinstance(val, int):
            folder_counts[f] = val
        else:
            folder_counts[f] = 0
    
    print(f"📊 当前计数: {folder_counts}")
    
    current_id = progress.get("last_id", START_ID - 1) + 1
    print(f"📍 从 ID {current_id} 开始\n")
    
    # 准备本地目录
    if os.path.exists(LOCAL_DIR):
        shutil.rmtree(LOCAL_DIR)
    ensure_dir(LOCAL_DIR)
    
    upload_queue = []
    last_success_id = current_id - 1
    consecutive_404 = 0
    
    # ========== 阶段1: 本地处理 ==========
    print("=" * 60)
    print("📥 阶段1: 本地下载和处理")
    print("=" * 60)
    
    while True:
        result = process_page_local(
            current_id, 
            hash_registry, 
            folder_counts, 
            upload_queue
        )
        
        if result == "success":
            last_success_id = current_id
            consecutive_404 = 0
            current_id += 1
            
        elif result == "video":
            # 视频页面，跳过继续
            last_success_id = current_id  # 也算处理过了
            consecutive_404 = 0
            current_id += 1
            
        elif result == "404":
            consecutive_404 += 1
            print(f"⚠️ 404 (连续: {consecutive_404}/{MAX_404_COUNT})")
            
            if consecutive_404 >= MAX_404_COUNT:
                print(f"\n⏹️ 连续 {MAX_404_COUNT} 个404，到达末尾")
                break
            
            current_id += 1
            
        else:
            # 出错
            print(f"\n❌ 处理出错，停止")
            break
    
    # 清理临时目录
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)
    
    # ========== 阶段2: 批量上传 ==========
    print("\n" + "=" * 60)
    print("📤 阶段2: 批量上传到 GitHub")
    print("=" * 60)
    
    if upload_queue:
        print(f"\n📊 待上传: {len(upload_queue)} 个文件")
        for f in FOLDERS:
            count = sum(1 for item in upload_queue if f"/{f}/" in item["remote_path"])
            if count > 0:
                print(f"   {f}: {count} 张")
        
        batch_upload_to_github(
            upload_queue, 
            hash_registry, 
            folder_counts, 
            last_success_id
        )
    else:
        print("\n📭 没有新图片")
        # 仍然更新进度
        progress["last_id"] = last_success_id
        save_remote_json("progress.json", progress, f"Update progress to {last_success_id}")
    
    # 清理
    if os.path.exists(LOCAL_DIR):
        shutil.rmtree(LOCAL_DIR)
    
    print("\n🏁 完成")


if __name__ == "__main__":
    main()
