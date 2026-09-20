import os
import re
import json
import base64
import time
import requests

GH_TOKEN = os.environ.get("GH_TOKEN", "").strip()
TARGET_REPO = os.environ.get("TARGET_REPO", "").strip()

if not GH_TOKEN:
    raise SystemExit("缺少 GH_TOKEN")
if not TARGET_REPO or "/" not in TARGET_REPO:
    raise SystemExit("缺少 TARGET_REPO，格式应为 owner/repo")

API_BASE = f"https://api.github.com/repos/{TARGET_REPO}"
HEADERS = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "count-updater",
}

_DEFAULT_BRANCH = None


def api_get(url, retries=5):
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)

            if r.status_code == 200:
                return r.json()

            if r.status_code == 404:
                print(f"404: {url}")
                return None

            if r.status_code in (403, 429):
                retry_after = r.headers.get("Retry-After")
                wait = int(retry_after) if retry_after and retry_after.isdigit() else min(2 ** i, 30)
                print(f"请求受限 {r.status_code}，等待 {wait}s: {url}")
                time.sleep(wait)
                continue

            print(f"请求失败 {r.status_code}: {url} | {r.text[:300]}")

        except Exception as e:
            print(f"请求异常: {e} | {url}")

        time.sleep(min(2 ** i, 30))

    return None


def get_default_branch():
    global _DEFAULT_BRANCH

    if _DEFAULT_BRANCH:
        return _DEFAULT_BRANCH

    data = api_get(API_BASE)
    if not data:
        raise RuntimeError("无法获取仓库信息，请检查 TARGET_REPO 和 GH_TOKEN")

    _DEFAULT_BRANCH = data.get("default_branch", "main")
    return _DEFAULT_BRANCH


def get_root_tree_sha():
    branch = get_default_branch()

    ref = api_get(f"{API_BASE}/git/ref/heads/{branch}")

    if ref:
        commit_sha = ref["object"]["sha"]
        commit = api_get(f"{API_BASE}/git/commits/{commit_sha}")
        if not commit:
            raise RuntimeError("无法获取 commit 信息")
        return commit["tree"]["sha"]

    # 兼容部分情况：直接用分支名取最新 commit
    commit = api_get(f"{API_BASE}/commits/{branch}")
    if not commit:
        raise RuntimeError(f"无法获取分支 {branch} 的最新提交")

    return commit["commit"]["tree"]["sha"]


def get_tree_item(tree_sha, name):
    """
    在指定 tree 中查找 name。
    返回 (sha, type)，找不到返回 (None, None)。
    请求失败会抛异常。
    """
    data = api_get(f"{API_BASE}/git/trees/{tree_sha}")

    if data is None:
        raise RuntimeError(f"获取 tree 失败: {tree_sha}")

    for item in data.get("tree", []):
        if item["path"] == name:
            return item["sha"], item["type"]

    return None, None


def get_folder_files(folder, ri_sha):
    """
    返回：
      list[int] -> 成功
      []        -> 目录不存在或空
      None      -> 获取失败，不要更新 count.json
    """
    try:
        folder_sha, folder_type = get_tree_item(ri_sha, folder)
    except Exception as e:
        print(f"{folder}: 获取目录信息失败: {e}")
        return None

    if not folder_sha or folder_type != "tree":
        print(f"{folder}: 找不到目录 ri/{folder}，按空目录处理")
        return []

    tree = api_get(f"{API_BASE}/git/trees/{folder_sha}")
    if tree is None:
        print(f"{folder}: 获取目录 tree 失败")
        return None

    files = []
    for item in tree.get("tree", []):
        if item["type"] == "blob":
            m = re.match(r"^(\d+)\.webp$", item["path"])
            if m:
                files.append(int(m.group(1)))

    return sorted(files)


def get_existing_count_json():
    r = requests.get(
        f"{API_BASE}/contents/ri/count.json",
        headers=HEADERS,
        timeout=30,
    )

    if r.status_code == 200:
        return r.json().get("sha")

    if r.status_code == 404:
        return None

    print(f"获取 count.json SHA 失败: {r.status_code} {r.text[:300]}")
    return None


def update_count_json(content, sha=None):
    data = {
        "message": "Auto update count.json",
        "content": base64.b64encode(content.encode()).decode(),
    }

    if sha:
        data["sha"] = sha

    r = requests.put(
        f"{API_BASE}/contents/ri/count.json",
        headers=HEADERS,
        json=data,
        timeout=30,
    )

    if r.status_code in (200, 201):
        return True

    print(f"更新 count.json 失败: {r.status_code} {r.text[:300]}")
    return False


def main():
    folders = ["hd", "hl", "vd", "vl"]
    result = {}
    failed = False

    try:
        root_tree_sha = get_root_tree_sha()
        ri_sha, ri_type = get_tree_item(root_tree_sha, "ri")
    except Exception as e:
        raise SystemExit(f"初始化失败: {e}")

    if not ri_sha or ri_type != "tree":
        raise SystemExit("找不到 ri 目录，已中止")

    for folder in folders:
        files = get_folder_files(folder, ri_sha)

        if files is None:
            print(f"{folder}: 获取失败")
            failed = True
            continue

        if not files:
            result[folder] = {"max": 0, "exclude": []}
            print(f"{folder}: 空")
            continue

        max_num = max(files)
        file_set = set(files)
        exclude = [i for i in range(1, max_num + 1) if i not in file_set]

        result[folder] = {"max": max_num, "exclude": exclude}
        print(f"{folder}: max={max_num}, files={len(files)}, excluded={len(exclude)}")

    if failed:
        raise SystemExit("有目录获取失败，已中止，避免覆盖 count.json")

    content = json.dumps(result, separators=(",", ":"), ensure_ascii=False)

    sha = get_existing_count_json()

    if update_count_json(content, sha):
        print("✅ count.json 更新成功!")
    else:
        print("❌ count.json 更新失败!")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
