import base64
import json
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

APP_TITLE = "地理模型制作大赛 · 观众投票"
LOCAL_VOTES_PATH = Path("data/public_votes.json")
WORKS_PATH = Path("data/works.csv")
TZ = timezone(timedelta(hours=8))


# ── helpers ──────────────────────────────────────────────────────────────────

def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def secret(section, key, default=None):
    try:
        return st.secrets.get(section, {}).get(key, default)
    except Exception:
        return default


def split_paths(value):
    return [x.strip() for x in str(value or "").split(";") if x.strip()]


@st.cache_data(show_spinner=False)
def load_works():
    df = pd.read_csv(WORKS_PATH, dtype={"work_id": str}).fillna("")
    df["work_id"] = df["work_id"].astype(str).str.zfill(2)
    return df


# ── GitHub 存储（与评分系统共享 token/repo，但写入不同文件） ──────────────

def github_configured():
    return bool(secret("github", "token") and secret("github", "repo"))


def _gh_headers():
    return {
        "Authorization": f"Bearer {secret('github', 'token')}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _gh_url():
    repo = secret("github", "repo")
    branch = secret("github", "branch", "main")
    path = "data/public_votes.json"
    return f"https://api.github.com/repos/{repo}/contents/{path}", branch


def _gh_load():
    url, branch = _gh_url()
    r = requests.get(url, headers=_gh_headers(), params={"ref": branch}, timeout=20)
    if r.status_code == 404:
        return {"votes": []}, None
    r.raise_for_status()
    payload = r.json()
    raw = base64.b64decode(payload["content"]).decode("utf-8")
    data = json.loads(raw)
    data.setdefault("votes", [])
    return data, payload.get("sha")


def _gh_save(data, message):
    url, branch = _gh_url()
    _, sha = _gh_load()
    content = base64.b64encode(
        json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    ).decode("utf-8")
    body = {"message": message, "content": content, "branch": branch}
    if sha:
        body["sha"] = sha
    r = requests.put(url, headers=_gh_headers(), json=body, timeout=30)
    if r.status_code == 409:
        time.sleep(1)
        _, sha = _gh_load()
        if sha:
            body["sha"] = sha
        r = requests.put(url, headers=_gh_headers(), json=body, timeout=30)
    r.raise_for_status()


def load_votes():
    if github_configured():
        return _gh_load()[0]
    if not LOCAL_VOTES_PATH.exists():
        LOCAL_VOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOCAL_VOTES_PATH.write_text(
            json.dumps({"votes": []}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    data = json.loads(LOCAL_VOTES_PATH.read_text(encoding="utf-8"))
    data.setdefault("votes", [])
    return data


def save_votes(data, message):
    if github_configured():
        _gh_save(data, message)
    else:
        LOCAL_VOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOCAL_VOTES_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )


# ── 页面：投票 ────────────────────────────────────────────────────────────────

def page_vote():
    st.markdown("### 填写你的信息")
    st.caption("每位同学只能投票一次，请如实填写，信息仅用于核验资格。")

    col1, col2 = st.columns(2)
    with col1:
        voter_name = st.text_input("姓名 *", placeholder="请填写真实姓名", key="voter_name")
    with col2:
        voter_phone = st.text_input("手机号 *", placeholder="11 位手机号", key="voter_phone")

    st.divider()
    st.markdown("### 浏览作品，选出你最喜欢的一件")

    works = load_works()

    # 用 session_state 记录已选队伍
    if "selected_work_id" not in st.session_state:
        st.session_state.selected_work_id = None

    # 2 列卡片（内容更多，2列留出足够宽度）
    cols = st.columns(2)
    for idx, (_, work) in enumerate(works.iterrows()):
        wid = work["work_id"]
        is_selected = st.session_state.selected_work_id == wid
        with cols[idx % 2]:
            border_color = "#1d72b8" if is_selected else "#e0e0e0"
            bg_color     = "#eef5ff"  if is_selected else "#fafafa"
            st.markdown(
                f"""<div style="border:2px solid {border_color};border-radius:10px;
                padding:12px 14px 4px 14px;margin-bottom:4px;background:{bg_color}">
                <b style="font-size:1.05em">{wid} | {work['team']}</b><br>
                <span style='color:#555;font-size:0.88em'>{work['title']}</span>
                </div>""",
                unsafe_allow_html=True,
            )

            # 全部图片：2 列小图排列
            imgs = split_paths(work.get("image_paths", ""))
            valid_imgs = [p for p in imgs if p.startswith("http") or Path(p).exists()]
            if valid_imgs:
                img_cols = st.columns(min(len(valid_imgs), 2))
                for i, img_path in enumerate(valid_imgs):
                    with img_cols[i % 2]:
                        st.image(img_path, use_container_width=True, caption=f"图 {i+1}")

            # 视频（折叠展示，避免页面过长）
            vids = split_paths(work.get("video_paths", ""))
            valid_vids = [v for v in vids if v.startswith("http") or Path(v).exists()]
            if valid_vids:
                with st.expander("▶ 查看视频"):
                    for v in valid_vids:
                        st.video(v)

            btn_label = "✅ 已选择此作品" if is_selected else "投这一票"
            btn_type  = "primary" if is_selected else "secondary"
            if st.button(btn_label, key=f"btn_{wid}", type=btn_type, use_container_width=True):
                st.session_state.selected_work_id = wid
                st.rerun()
            st.markdown("<div style='margin-bottom:20px'></div>", unsafe_allow_html=True)

    # 提交区域
    selected_id = st.session_state.selected_work_id
    if selected_id:
        sel_work = works[works["work_id"] == selected_id].iloc[0]
        st.success(f"你选择了：**{selected_id} | {sel_work['team']}——{sel_work['title']}**")

    st.divider()
    if st.button("确认提交投票", type="primary", disabled=not selected_id):
        name = (voter_name or "").strip()
        phone = (voter_phone or "").strip()

        if not name:
            st.error("请填写姓名。")
            st.stop()
        if not re.match(r"^1[3-9]\d{9}$", phone):
            st.error("请填写正确的 11 位手机号。")
            st.stop()

        data = load_votes()
        existing_phones = {v.get("voter_phone") for v in data["votes"]}
        if phone in existing_phones:
            st.error("该手机号已投过票，每位同学只能投票一次。")
            st.stop()

        work = works[works["work_id"] == selected_id].iloc[0]
        record = {
            "vote_id": str(uuid.uuid4()),
            "voted_at": now_str(),
            "voter_name": name,
            "voter_phone": phone,
            "work_id": selected_id,
            "team": work["team"],
            "title": work["title"],
        }
        data["votes"].append(record)
        save_votes(data, f"vote: {phone[:7]}*** -> {selected_id}")

        st.session_state.selected_work_id = None
        st.success(
            f"🎉 投票成功！你投票给了：**{selected_id} | {work['team']}——{work['title']}**\n\n"
            "感谢参与，每位同学只有一次投票机会。"
        )
        st.balloons()


# ── 页面：管理后台 ─────────────────────────────────────────────────────────────

def _votes_to_excel(detail_df, stat_df):
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        stat_df.to_excel(writer, index=False, sheet_name="投票统计")
        detail_df.to_excel(writer, index=False, sheet_name="投票明细")
    return buf.getvalue()


def page_admin():
    st.header("投票管理后台")
    pwd = st.text_input("管理员密码", type="password")
    if pwd != secret("app", "admin_password", "admin123"):
        st.info("请输入管理员密码查看投票数据。")
        return

    data = load_votes()
    votes = data.get("votes", [])

    st.metric("当前总投票数", len(votes))

    if not votes:
        st.warning("暂无投票记录。")
        return

    raw_df = pd.DataFrame(votes)

    # 统计表
    stat_df = (
        raw_df.groupby(["work_id", "team", "title"], as_index=False)
        .size()
        .rename(columns={"size": "票数"})
        .sort_values("票数", ascending=False)
        .reset_index(drop=True)
    )
    stat_df.index += 1

    # 明细表（脱敏：只显示手机号前 3 后 4 位）
    detail_df = raw_df[["voted_at", "voter_name", "voter_phone", "work_id", "team", "title"]].copy()
    detail_df.columns = ["投票时间", "姓名", "手机号", "作品编号", "队伍", "作品名"]
    detail_export = detail_df.copy()  # 导出版完整手机号
    detail_df["手机号"] = detail_df["手机号"].apply(
        lambda p: p[:3] + "****" + p[-4:] if len(p) == 11 else p
    )

    tab1, tab2, tab3 = st.tabs(["投票统计", "投票明细", "导出 & 管理"])

    with tab1:
        st.dataframe(stat_df, use_container_width=True)

    with tab2:
        st.caption("手机号已做脱敏处理（前 3 后 4），导出文件含完整号码。")
        st.dataframe(detail_df, use_container_width=True, hide_index=True)

    with tab3:
        st.download_button(
            "下载投票明细 CSV（含完整手机号）",
            detail_export.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
            "投票明细.csv",
            "text/csv",
        )
        st.download_button(
            "下载投票统计 CSV",
            stat_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
            "投票统计.csv",
            "text/csv",
        )
        st.download_button(
            "下载 Excel 总表",
            _votes_to_excel(detail_export, stat_df),
            "投票汇总.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.divider()
        confirm = st.text_input("如需清空全部投票，请输入：清空投票")
        if st.button("清空全部投票记录", type="primary"):
            if confirm == "清空投票":
                save_votes({"votes": []}, "reset all public votes")
                st.success("已清空全部投票记录。")
                st.rerun()
            else:
                st.error("确认文字不正确，未执行。")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🗳️", layout="wide")
    st.title(APP_TITLE)

    with st.sidebar:
        st.title("导航")
        if github_configured():
            st.success("数据存储：GitHub")
        else:
            st.warning("数据存储：本地文件")
        page = st.radio("页面", ["投票", "管理后台"])

    try:
        if page == "投票":
            page_vote()
        else:
            page_admin()
    except Exception as e:
        st.error("系统出现错误。")
        st.exception(e)


if __name__ == "__main__":
    main()
