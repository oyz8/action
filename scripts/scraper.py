#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图片爬虫 + count.json 维护 + Cloudflare Pages Deploy Hook（合并版）
"""

import os
import re
import json
import time
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

START_ID = 342
MAX_404_COUNT = 5

TARGET_REPO = os.environ.get("TARGET_REPO", "").strip()
GITHUB_TOKEN = os.environ.get("GH_TOKEN", "").strip()
TARGET_BRANCH = os.environ.get("TARGET_BRANCH", "main").strip()

# Cloudflare Pages Deploy Hook URL（可空）
CF_DEPLOY_HOOK = os.environ.get("CF_DEPLOY_HOOK", "").strip()

IMAGES_DIR = "ri"
FOLDERS = ["hd", "hl", "vd", "vl"]

PROGRESS_PATH = "progress.json"
COUNT_PATH = f"{IMAGES_DIR}/count.json"
HASH_PATH = f"{IMAGES_DIR}/hash_registry.json"

API_BASE = f"https://api.github.com/repos/{TARGET_REPO}"
HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "img-scraper",
}

scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False}
)


# ============ GitHub API ============

def api_request(method: str, url: str, retries: int = 5, **kwargs):
    """统一 GitHub API 请求，带自动重试。返回 response 或 None。"""
    for i in range(retries):
        try:
            resp = requests.request(
                method, url, headers=HEADERS, timeout=30, **kwargs
            )
        except requests.RequestException as e:
            wait = min(2 ** i, 30)
            print(f"⚠️ 请求异常: {e}，{wait}s 后重试")
            time.sleep(wait)
            continue

        if resp.status_code in (200, 201, 404):
            return resp

        if resp.status_code == 403:
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                wait = int(retry_after)
                print(f"⚠️ 限流，等待 {wait}s")
                time.sleep(wait)
                continue
            return resp

        if resp.status_code == 429 or resp.status_code >= 500:
            wait = min(2 ** i, 30)
            print(f"⚠️ API {resp.status_code}，{wait}s 后重试: {url}")
            time.sleep(wait)
            continue

        return resp

    return None


def github_get_file(path: str):
    """返回 (content_str, sha)，不存在返回 (None, None)。"""
    resp = api_request("GET", f"{API_BASE}/contents/{path}")
    if resp is None or resp.status_code != 200:
        return None, None
    try:
        data = resp.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return content, data.get("sha")
    except Exception as e:
        print(f"⚠️ 解析 {path} 失败: {e}")
        return None, None


def github_upload(path: str, content: bytes, message: str, sha: str = None) -> bool:
    data = {
        "message": message,
        "content": base64.b64encode(content).decode("utf-8"),
        "branch": TARGET_BRANCH,
    }
    if sha:
        data["sha"] = sha

    resp = api_request("PUT", f"{API_BASE}/contents/{path}", retries=3, json=data)
    if resp is None:
        return False
    if resp.status_code in (200, 201):
        return True
    print(f"❌ 上传失败 {path}: {resp.status_code} {resp.text[:200]}")
    return False


def get_remote_json(path: str, default=None) -> dict:
    content, _ = github_get_file(path)
    if content:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
    return default if default is not None else {}


def save_remote_json_if_changed(path: str, data, message: str) -> bool:
    """内容没有变化就不提交，避免空 commit。"""
    old_content, sha = github_get_file(path)
    new_content = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    if old_content is not None:
        try:
            if json.loads(old_content) == data:
                print(f"⏭️ {path} 无变化，跳过")
                return True
        except json.JSONDecodeError:
            pass

    return github_upload(path, new_content.encode("utf-8"), message, sha)


# ============ count.json 处理 ============

def load_count(raw: dict) -> dict:
    result = {}
    for f in FOLDERS:
        val = raw.get(f)
        if isinstance(val, dict):
            max_n = int(val.get("max", 0))
            exclude = sorted({int(x) for x in val.get("exclude", []) if 1 <= int(x) <= max_n})
            result[f] = {"max": max_n, "exclude": exclude}
        elif isinstance(val, int):
            result[f] = {"max": val, "exclude": []}
        else:
            result[f] = {"max": 0, "exclude": []}
    return result


def allocate_number(counter: dict) -> int:
    if counter["exclude"]:
        return counter["exclude"].pop(0)
    counter["max"] += 1
    return counter["max"]


def release_number(counter: dict, num: int):
    if num not in counter["exclude"] and num <= counter["max"]:
        counter["exclude"].append(num)
        counter["exclude"].sort()


def get_tree_item(tree_sha: str, name: str):
    resp = api_request("GET", f"{API_BASE}/git/trees/{tree_sha}")
    if resp is None or resp.status_code != 200:
        return None, None
    for item in resp.json().get("tree", []):
        if item["path"] == name:
            return item["sha"], item["type"]
    return None, None


def get_root_tree_sha():
    resp = api_request("GET", f"{API_BASE}/git/ref/heads/{TARGET_BRANCH}")
    if resp is not None and resp.status_code == 200:
        commit_sha = resp.json()["object"]["sha"]
        c = api_request("GET", f"{API_BASE}/git/commits/{commit_sha}")
        if c is not None and c.status_code == 200:
            return c.json()["tree"]["sha"]

    c = api_request("GET", f"{API_BASE}/commits/{TARGET_BRANCH}")
    if c is not None and c.status_code == 200:
        return c.json()["commit"]["tree"]["sha"]

    return None


def scan_remote_count():
    """逐级扫描远程 ri/{folder}，重建 count 结构。失败返回 None。"""
    root_sha = get_root_tree_sha()
    if not root_sha:
        print("❌ 无法获取根 tree")
        return None

    ri_sha, ri_type = get_tree_item(root_sha, IMAGES_DIR)
    if not ri_sha or ri_type != "tree":
        print(f"❌ 找不到 {IMAGES_DIR} 目录")
        return None

    result = {}
    for folder in FOLDERS:
        folder_sha, folder_type = get_tree_item(ri_sha, folder)
        if not folder_sha or folder_type != "tree":
            result[folder] = {"max": 0, "exclude": []}
            continue

        tree = api_request("GET", f"{API_BASE}/git/trees/{folder_sha}")
        if tree is None or tree.status_code != 200:
            print(f"❌ 获取 {folder} tree 失败")
            return None

        nums = set()
        for item in tree.json().get("tree", []):
            if item["type"] != "blob":
                continue
            m = re.match(r"^(\d+)\.webp$", item["path"])
            if m:
                nums.add(int(m.group(1)))

        if not nums:
            result[folder] = {"max": 0, "exclude": []}
        else:
            max_n = max(nums)
            result[folder] = {
                "max": max_n,
                "exclude": [i for i in range(1, max_n + 1) if i not in nums],
            }

    return result


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

def scrape_images(url: str):
    print(f"🌐 爬取: {url}")
    try:
        resp = scraper.get(url, timeout=30)
        if resp.status_code == 404:
            return [], "404"
        resp.raise_for_status()
        resp.encoding = "utf-8"
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
        print("🎬 无图片（视频页面），跳过")
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
        return cv2.imwrite(output_path, img, [cv2.IMWRITE_WEBP_QUALITY, 85])
    except Exception:
        return False


def analyze_image(path: str):
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


# ============ 页面处理 ============

def process_page_local(page_id: int, hash_registry: dict, count: dict,
                       upload_queue: list) -> str:
    url = build_url(page_id)
    print(f"\n{'=' * 50}")
    print(f"📂 页面 ID: {page_id}")
    print(f"{'=' * 50}")

    ensure_dir(TEMP_DIR)
    images, status = scrape_images(url)
    if status != "ok":
        return status

    new_count = 0
    for img in images[:BATCH_SIZE]:
        idx = img["index"]
        temp_path = os.path.join(TEMP_DIR, f"temp_{page_id}_{idx}")

        print(f"📥 [{idx}/{min(len(images), BATCH_SIZE)}] 下载中...")
        if not download_image(img["url"], temp_path):
            continue

        file_hash = get_file_hash(temp_path)
        if file_hash in hash_registry:
            print("  ⏭️ 跳过重复")
            os.remove(temp_path)
            continue

        info = analyze_image(temp_path)
        if not info:
            os.remove(temp_path)
            continue

        target_folder = info["folder"]
        new_num = allocate_number(count[target_folder])

        local_folder = os.path.join(LOCAL_DIR, IMAGES_DIR, target_folder)
        ensure_dir(local_folder)
        local_path = os.path.join(local_folder, f"{new_num}.webp")

        if not convert_to_webp(temp_path, local_path):
            os.remove(temp_path)
            release_number(count[target_folder], new_num)
            continue
        os.remove(temp_path)

        remote_path = f"{IMAGES_DIR}/{target_folder}/{new_num}.webp"
        upload_queue.append({
            "local_path": local_path,
            "remote_path": remote_path,
            "hash": file_hash,
        })
        hash_registry[file_hash] = f"{target_folder}/{new_num}.webp"
        new_count += 1
        print(f"  💾 {local_path}")

    print(f"✅ 页面 {page_id} 完成，新增 {new_count} 张")
    return "success"


def batch_upload_to_github(upload_queue: list, hash_registry: dict):
    """返回 (success, fail)。"""
    print(f"\n{'=' * 60}")
    print(f"📤 开始批量上传 {len(upload_queue)} 个文件")
    print(f"{'=' * 60}\n")

    success = 0
    fail = 0

    for idx, item in enumerate(upload_queue, 1):
        print(f"[{idx}/{len(upload_queue)}] {item['remote_path']}", end=" ")
        try:
            with open(item["local_path"], "rb") as f:
                content = f.read()
            if github_upload(item["remote_path"], content,
                             f"Add {item['remote_path']}"):
                success += 1
                print("✅")
            else:
                fail += 1
                print("❌")
                hash_registry.pop(item["hash"], None)
        except Exception as e:
            fail += 1
            print(f"❌ {e}")
            hash_registry.pop(item["hash"], None)

    print(f"\n📊 上传完成: 成功 {success}, 失败 {fail}")
    return success, fail


# ============ Cloudflare Deploy Hook ============

def trigger_cf_deploy() -> bool:
    if not CF_DEPLOY_HOOK:
        print("⏭️ 未配置 CF_DEPLOY_HOOK，跳过")
        return False

    print(f"🚀 触发 Deploy Hook...")
    for i in range(3):
        try:
            resp = requests.post(CF_DEPLOY_HOOK, timeout=30)
            if resp.status_code in (200, 201):
                print(f"✅ Deploy Hook 已触发: {resp.status_code}")
                return True
            print(f"⚠️ Deploy Hook 返回 {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"⚠️ Deploy Hook 请求异常: {e}")
        time.sleep(2 ** i)

    print("❌ Deploy Hook 触发失败")
    return False


# ============ 主函数 ============

def main():
    print("🚀 开始运行\n")

    if not GITHUB_TOKEN:
        raise SystemExit("❌ 缺少 GH_TOKEN")
    if not TARGET_REPO or "/" not in TARGET_REPO:
        raise SystemExit("❌ 缺少 TARGET_REPO (owner/repo)")

    print(f"📦 目标仓库: {TARGET_REPO}")
    print(f"📁 存储目录: /{IMAGES_DIR}/")
    if CF_DEPLOY_HOOK:
        print(f"🌐 CF Deploy Hook: 已配置")
    else:
        print(f"🌐 CF Deploy Hook: 未配置（跳过）")
    print()

    # ---- 加载远程状态 ----
    print("📥 获取远程数据...")
    progress = get_remote_json(PROGRESS_PATH, {"last_id": START_ID - 1})
    hash_registry = get_remote_json(HASH_PATH, {})
    raw_count = get_remote_json(COUNT_PATH, {})
    count = load_count(raw_count)

    print("📊 当前计数:")
    for f in FOLDERS:
        c = count[f]
        print(f"   {f}: max={c['max']}, exclude={len(c['exclude'])}")

    current_id = progress.get("last_id", START_ID - 1) + 1
    print(f"📍 从 ID {current_id} 开始\n")

    # ---- 清理本地 ----
    for d in (LOCAL_DIR, TEMP_DIR):
        if os.path.exists(d):
            shutil.rmtree(d)
    ensure_dir(LOCAL_DIR)

    # ---- 阶段1: 本地处理 ----
    print("=" * 60)
    print("📥 阶段1: 本地下载和处理")
    print("=" * 60)

    upload_queue = []
    last_success_id = current_id - 1
    consecutive_404 = 0

    while True:
        result = process_page_local(current_id, hash_registry, count, upload_queue)

        if result in ("success", "video"):
            last_success_id = current_id
            consecutive_404 = 0
            current_id += 1
        elif result == "404":
            consecutive_404 += 1
            print(f"⚠️ 404 (连续: {consecutive_404}/{MAX_404_COUNT})")
            if consecutive_404 >= MAX_404_COUNT:
                print(f"\n⏹️ 连续 {MAX_404_COUNT} 个 404，到达末尾")
                break
            current_id += 1
        else:
            print("\n❌ 处理出错，停止")
            break

    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)

    # ---- 阶段2: 上传 ----
    print("\n" + "=" * 60)
    print("📤 阶段2: 批量上传到 GitHub")
    print("=" * 60)

    uploaded_count = 0
    all_ok = True
    if upload_queue:
        print(f"\n📊 待上传: {len(upload_queue)} 个文件")
        for f in FOLDERS:
            n = sum(1 for it in upload_queue if f"/{f}/" in it["remote_path"])
            if n:
                print(f"   {f}: {n} 张")
        uploaded_count, fail = batch_upload_to_github(upload_queue, hash_registry)
        all_ok = (fail == 0)
        save_remote_json_if_changed(HASH_PATH, hash_registry,
                                    "Update hash_registry")
    else:
        print("\n📭 没有新图片")

    # ---- 阶段3: 重建 count.json ----
    print("\n" + "=" * 60)
    print("🔢 阶段3: 重建 count.json")
    print("=" * 60)

    new_count = scan_remote_count()
    if new_count is not None:
        save_remote_json_if_changed(COUNT_PATH, new_count, "Update count.json")
        for f in FOLDERS:
            c = new_count[f]
            print(f"   {f}: max={c['max']}, exclude={len(c['exclude'])}")
    else:
        print("⚠️ 远程扫描失败，count.json 未更新")

    # ---- 阶段4: 更新 progress ----
    if all_ok:
        progress["last_id"] = last_success_id
        save_remote_json_if_changed(
            PROGRESS_PATH, progress, f"Update progress to {last_success_id}"
        )
    else:
        print("⚠️ 存在上传失败，progress.json 不前移")

    # ---- 阶段5: 触发 Cloudflare Pages Deploy Hook ----
    print("\n" + "=" * 60)
    print("🚀 阶段5: 触发 Cloudflare Pages Deploy Hook")
    print("=" * 60)

    if not all_ok:
        print("⏭️ 存在上传失败，跳过 Deploy Hook")
    elif uploaded_count == 0:
        print("⏭️ 本轮没有新文件上传，跳过 Deploy Hook")
    else:
        trigger_cf_deploy()

    # ---- 清理 ----
    if os.path.exists(LOCAL_DIR):
        shutil.rmtree(LOCAL_DIR)

    print("\n🏁 完成")


if __name__ == "__main__":
    main()
