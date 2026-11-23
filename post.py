"""끼끼 경매툰 자동 게시 (GitHub Actions에서 실행)

python post.py post      다음 회차 1개 게시 (기본)
python post.py dry       무엇을 올릴지만 출력
python post.py discover  토큰으로 접근 가능한 인스타 계정 ID 출력

환경변수: IG_TOKEN(필수, GitHub Secret) / IG_USER_ID(없으면 자동 탐색) / GRAPH_VER / BUSINESS_ID
queue/<회차>/ 의 *.jpg(최대 10장, 이름순) + caption.txt 를 캐러셀로 올리고 state.json 에 기록.
"""
import json, os, sys, time, urllib.error, urllib.parse, urllib.request

VER = os.environ.get("GRAPH_VER", "v23.0")
API = f"https://graph.facebook.com/{VER}"
BIZ = os.environ.get("BUSINESS_ID", "1129033332861105")
HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(HERE, "queue")
STATE = os.path.join(HERE, "state.json")


def call(method, path, soft=False, **params):
    """soft=True 면 오류 때 종료하지 않고 None (게시 직후 확인용)"""
    params["access_token"] = os.environ["IG_TOKEN"]
    data = urllib.parse.urlencode(params).encode()
    url = f"{API}/{path}"
    req = urllib.request.Request(url + ("?" + data.decode() if method == "GET" else ""),
                                 data=None if method == "GET" else data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        msg = f"API 오류 {e.code} {path}: {e.read().decode(errors='replace')[:600]}"
        if soft:
            print(msg)
            return None
        sys.exit(msg)


def publish(ig, cid, caption, strict=True):
    """media_publish 가 오류를 돌려줘도 실제로는 올라가는 경우가 있음 (11/20 9화, 11/23 1화 둘 다 게시됐는데 작업 실패
    → 기록 누락 → 다음 실행에서 중복 게시 위험). 오류면 최근 게시물 캡션을 확인해서 올라갔으면 성공으로 처리."""
    r = call("POST", f"{ig}/media_publish", soft=True, creation_id=cid)
    if r:
        return r
    time.sleep(20)
    recent = call("GET", f"{ig}/media", fields="id,caption,timestamp", limit=3).get("data", [])
    head = caption.strip()[:40]
    hit = next((m for m in recent if (m.get("caption") or "").strip().startswith(head)), None)
    if hit:
        print("publish 오류였지만 실제로 게시됨:", hit)
        return hit
    if not strict:  # 릴스(피드 공유 X)는 최근 게시물 목록에 안 잡힐 수 있음 → 중복 게시보다 기록 쪽이 안전
        print("::warning::릴스 publish 오류, 게시 여부 확인 못 함 → 게시된 것으로 기록 (인스타에서 확인 필요)")
        return None
    sys.exit("게시 실패 (최근 게시물에도 없음)")


def ig_user_id():
    if os.environ.get("IG_USER_ID"):
        return os.environ["IG_USER_ID"]
    return "17841469832063842"  # @kki.kki.home (비즈니스 설정 > Instagram 계정에 표시된 ID)
    # 토큰에 허용된 인스타 계정 ID (business_management 권한 없이 확인 가능)
    info = call("GET", "debug_token", input_token=os.environ["IG_TOKEN"]).get("data", {})
    for s in info.get("granular_scopes", []):
        if s.get("scope") in ("instagram_content_publish", "instagram_basic") and s.get("target_ids"):
            return s["target_ids"][0]
    sys.exit(f"인스타 계정을 찾지 못함 (토큰 권한: {info.get('scopes')})")


def next_episode(state):
    eps = sorted(d for d in os.listdir(QUEUE) if os.path.isdir(os.path.join(QUEUE, d)))
    return next((e for e in eps if e not in state["posted"]), None)


def wait_ready(cid):
    for _ in range(30):
        s = call("GET", cid, fields="status_code").get("status_code")
        if s == "FINISHED":
            return
        if s in ("ERROR", "EXPIRED"):
            sys.exit(f"컨테이너 {cid} 상태 {s}")
        time.sleep(10)
    sys.exit(f"컨테이너 {cid} 준비 시간 초과")


def post(ep, ig, dry=False):
    folder = os.path.join(QUEUE, ep)
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".jpg"))[:10]
    caption = open(os.path.join(folder, "caption.txt"), encoding="utf-8").read().strip()
    base = f"https://raw.githubusercontent.com/{os.environ.get('GITHUB_REPOSITORY', 'OWNER/REPO')}/" \
           f"{os.environ.get('GITHUB_REF_NAME', 'main')}/queue/{ep}"
    urls = [f"{base}/{f}" for f in files]
    print(f"{ep}: {len(urls)}장\n" + "\n".join(urls) + f"\n--- 캡션 ---\n{caption}")
    if dry:
        return
    children = []
    for u in urls:
        cid = call("POST", f"{ig}/media", image_url=u, is_carousel_item="true")["id"]
        wait_ready(cid)
        children.append(cid)
    carousel = call("POST", f"{ig}/media", media_type="CAROUSEL", children=",".join(children), caption=caption)["id"]
    wait_ready(carousel)
    print("게시 완료:", publish(ig, carousel, caption))


def post_reel(ep, ig):
    """reels/<ep>.mp4 + reels/<ep>.txt 를 릴스로 게시 (피드 공유 X)"""
    caption = open(os.path.join(HERE, "reels", f"{ep}.txt"), encoding="utf-8").read().strip()
    url = f"https://raw.githubusercontent.com/{os.environ['GITHUB_REPOSITORY']}/" \
          f"{os.environ.get('GITHUB_REF_NAME', 'main')}/reels/{ep}.mp4"
    print(f"릴스 {ep}: {url}")
    cid = call("POST", f"{ig}/media", media_type="REELS", video_url=url, caption=caption,
               share_to_feed="false")["id"]
    wait_ready(cid)
    print("릴스 게시 완료:", publish(ig, cid, caption, strict=False))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "post"
    state = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {"posted": []}
    state.setdefault("reels", [])
    if mode == "reel-next":  # 매일: 캐러셀이 이미 올라간 회차 중 릴스 안 올린 것 1개
        ready = sorted(f[:-4] for f in os.listdir(os.path.join(HERE, "reels")) if f.endswith(".mp4"))
        ep = next((e for e in ready if e not in state["reels"] and (e in state["posted"] or e == "ep01")), None)
        if not ep:
            print("올릴 릴스 없음")
            return
        post_reel(ep, ig_user_id())
        state["reels"].append(ep)
        json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return
    if mode == "reel":  # reel-now.txt 에 적힌 회차 1개
        ep = open(os.path.join(HERE, "reel-now.txt"), encoding="utf-8").read().split()[0]
        if ep in state["reels"]:
            print(f"{ep} 릴스는 이미 게시됨")
            return
        post_reel(ep, ig_user_id())
        state["reels"].append(ep)
        json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return
    if mode == "discover":
        ig = ig_user_id()
        print("인스타 계정:", call("GET", ig, fields="id,username"))
        return
    ep = next_episode(state)
    if not ep:
        print("올릴 회차 없음 (queue 비었음)")
        return
    post(ep, None if mode == "dry" else ig_user_id(), dry=(mode == "dry"))
    if mode == "post":
        state["posted"].append(ep)
        json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        # 같은 회차 릴스가 준비돼 있으면 캐러셀과 함께 게시
        if os.path.exists(os.path.join(HERE, "reels", f"{ep}.mp4")) and ep not in state["reels"]:
            post_reel(ep, ig_user_id())
            state["reels"].append(ep)
            json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
