"""
응급실 실시간 정보 수집기 (GitHub Actions에서 10분마다 실행)
- 국립중앙의료원_전국 응급의료기관 정보 조회 서비스 (공공데이터포털)
- 대전 + 세종 수집, 지역별로 파일을 나눠 저장
- 저장: data/YYYY-MM-DD_<기능>_<지역>.csv (하루 단위 파일에 이어쓰기)
"""
import csv
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import requests

KST = timezone(timedelta(hours=9))
BASE = "http://apis.data.go.kr/B552657/ErmctInfoInqireService/"
SERVICE_KEY = os.environ.get("SERVICE_KEY", "").strip()

# 지역코드: (시도명, 시도 단위 호출 실패 시 나눠 부를 시군구 목록)
REGIONS = {
    "daejeon": ("대전광역시", ["동구", "중구", "서구", "유성구", "대덕구"]),
    "sejong": ("세종특별자치시", ["세종특별자치시"]),
}

OPS = {
    "beds": "getEmrrmRltmUsefulSckbdInfoInqire",      # 응급실 실시간 가용병상
    "severe": "getSrsillDissAceptncPosblInfoInqire",  # 중증질환자 수용가능정보
}
MSG_OP = "getEmrrmSrsillDissMsgInqire"                # 응급실 및 중증질환 메시지 (기관별)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
FIELDS = ["collected_at", "op", "hpid", "dutyName", "item_idx", "field", "value"]


def call(op, params):
    p = {"serviceKey": SERVICE_KEY, "pageNo": 1, "numOfRows": 100, **params}
    r = requests.get(BASE + op, params=p, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode")
    if code not in (None, "00", "0000"):
        msg = root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg")
        raise RuntimeError(f"{op} 오류 {code}: {msg}")
    items = []
    for it in root.iter("item"):
        items.append({child.tag: (child.text or "").strip() for child in it})
    return items


def collect_region(op, sido, gu_list):
    """시도 단위로 먼저 시도하고, 결과가 없으면 시군구 단위로 나눠 호출"""
    try:
        items = call(op, {"STAGE1": sido})
        if items:
            return items
    except Exception as e:
        log(f"[{op}] {sido} 시도 단위 실패 → 시군구 단위로 재시도: {e}")
    items = []
    for gu in gu_list:
        try:
            items += call(op, {"STAGE1": sido, "STAGE2": gu})
        except Exception as e:
            log(f"[{op}] {sido} {gu} 실패: {e}")
    return items


def save(name, region, now, op_label, items):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{now:%Y-%m-%d}_{name}_{region}.csv")
    new = not os.path.exists(path)
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for i, it in enumerate(items):
            hpid, name_ = it.get("hpid", ""), it.get("dutyName", it.get("dutyname", ""))
            for k, v in it.items():
                w.writerow({"collected_at": ts, "op": op_label, "hpid": hpid,
                            "dutyName": name_, "item_idx": i, "field": k, "value": v})


def log(msg):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "errors.log"), "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(KST):%Y-%m-%d %H:%M:%S} {msg}\n")
    print(msg, file=sys.stderr)


def main():
    if not SERVICE_KEY:
        sys.exit("SERVICE_KEY 환경변수가 없습니다 (GitHub Secrets 확인)")
    now = datetime.now(KST)
    do_msg = now.minute < 10 or 30 <= now.minute < 40 or os.environ.get("FORCE_MSG")

    for region, (sido, gu_list) in REGIONS.items():
        hpids = set()
        for name, op in OPS.items():
            items = collect_region(op, sido, gu_list)
            save(name, region, now, op, items)
            hpids |= {it.get("hpid") for it in items if it.get("hpid")}
            print(f"{region} {name}: {len(items)}개 기관")

        # 메시지는 기관별 호출이라 30분에 한 번만
        if do_msg:
            msgs = []
            for h in sorted(hpids):
                try:
                    for it in call(MSG_OP, {"HPID": h}):
                        it.setdefault("hpid", h)
                        msgs.append(it)
                except Exception as e:
                    log(f"[msg] {h} 실패: {e}")
            save("messages", region, now, MSG_OP, msgs)
            print(f"{region} messages: {len(msgs)}건")


if __name__ == "__main__":
    main()
