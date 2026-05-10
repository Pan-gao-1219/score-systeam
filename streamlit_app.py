
import base64
import json
import time
import uuid
from datetime import datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

APP_TITLE = "地理模型制作大赛评分系统"
LOCAL_DATA_PATH = Path("data/scores.json")
WORKS_PATH = Path("data/works.csv")
TZ = timezone(timedelta(hours=8))

RUBRIC = [
    ("科学严谨性", [
        ("data_accuracy", "数据准确性", 10, "地理要素符合真实数据，比例尺、坐标等科学合理。"),
        ("theory_support", "理论支撑性", 15, "模型设计体现明确的地理科学原理。"),
        ("label_norm", "标注规范性", 10, "关键地理要素标注完整，图例、比例尺、说明文字规范易懂。"),
    ]),
    ("工艺技术水平", [
        ("material_engineering", "材料工程", 5, "材料选择符合环保、经济原则，与主题适配性强。"),
        ("craft_precision", "制作精度", 15, "结构稳固，切割、拼接、上色等细节处理精细。"),
        ("tech_difficulty", "技术难度", 5, "模型层次丰富，体现技术难度。"),
    ]),
    ("创新设计能力", [
        ("topic_innovation", "选题创新性", 15, "选题新颖，避免重复常见主题。"),
        ("design_unique", "设计独特性", 10, "细节创新，设计巧妙，能融合跨学科元素或提出创新方案。"),
    ]),
    ("展示传播效果", [
        ("visual_communication", "视觉传达", 10, "色彩搭配协调，整体布局和谐，具有视觉冲击力。"),
        ("info_communication", "信息传达", 5, "模型重点突出，观众能快速理解核心地理概念。"),
    ]),
]
SCORE_FIELDS = [item[0] for _, items in RUBRIC for item in items]


def secret(section, key, default=None):
    try:
        return st.secrets.get(section, {}).get(key, default)
    except Exception:
        return default


def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def split_paths(value):
    return [x.strip() for x in str(value or "").split(";") if x.strip()]


@st.cache_data(show_spinner=False)
def load_works():
    df = pd.read_csv(WORKS_PATH, dtype={"work_id": str}).fillna("")
    df["work_id"] = df["work_id"].astype(str).str.zfill(2)
    return df


def github_configured():
    return bool(secret("github", "token") and secret("github", "repo"))


def github_headers():
    return {
        "Authorization": f"Bearer {secret('github', 'token')}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def github_file_info():
    repo = secret("github", "repo")
    branch = secret("github", "branch", "main")
    path = secret("github", "scores_path", "data/scores.json")
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    return repo, branch, path, url


def load_data_from_github():
    _, branch, _, url = github_file_info()
    r = requests.get(url, headers=github_headers(), params={"ref": branch}, timeout=20)
    if r.status_code == 404:
        return {"scores": [], "votes": {}}, None
    r.raise_for_status()
    payload = r.json()
    raw = base64.b64decode(payload["content"]).decode("utf-8")
    data = json.loads(raw)
    data.setdefault("scores", [])
    data.setdefault("votes", {})
    return data, payload.get("sha")


def save_data_to_github(data, message):
    _, branch, _, url = github_file_info()
    _, sha = load_data_from_github()
    content = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")).decode("utf-8")
    body = {"message": message, "content": content, "branch": branch}
    if sha:
        body["sha"] = sha
    r = requests.put(url, headers=github_headers(), json=body, timeout=30)
    if r.status_code == 409:
        time.sleep(1)
        _, sha = load_data_from_github()
        if sha:
            body["sha"] = sha
        r = requests.put(url, headers=github_headers(), json=body, timeout=30)
    r.raise_for_status()


def load_data_local():
    if not LOCAL_DATA_PATH.exists():
        LOCAL_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOCAL_DATA_PATH.write_text(json.dumps({"scores": [], "votes": {}}, ensure_ascii=False, indent=2), encoding="utf-8")
    data = json.loads(LOCAL_DATA_PATH.read_text(encoding="utf-8"))
    data.setdefault("scores", [])
    data.setdefault("votes", {})
    return data


def save_data_local(data):
    LOCAL_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_data():
    if github_configured():
        return load_data_from_github()[0]
    return load_data_local()


def save_data(data, message):
    if github_configured():
        save_data_to_github(data, message)
    else:
        save_data_local(data)


def judge_id(name, code):
    name = (name or "").strip()
    code = (code or "").strip()
    return f"{name}-{code}" if code else name


def existing_score(scores, jid, work_id):
    for row in scores:
        if row.get("judge_id") == jid and row.get("work_id") == work_id:
            return row
    return None


def professional_score(row):
    raw = sum(safe_int(row.get(f, 0)) for f in SCORE_FIELDS)
    if row.get("common_error"):
        raw -= 10
    if row.get("no_process_evidence"):
        raw -= 20
    return max(0, min(100, raw))


def scores_dataframe(data):
    rows = []
    for row in data.get("scores", []):
        r = dict(row)
        r["professional_score"] = professional_score(r)
        rows.append(r)
    return pd.DataFrame(rows)


def build_summary(data, works):
    score_df = scores_dataframe(data)
    votes = data.get("votes", {})
    base = works[["work_id", "team", "title"]].copy()
    base["network_vote_score"] = base["work_id"].map(lambda x: float(votes.get(str(x), 0) or 0))
    if score_df.empty:
        base["judge_count"] = 0
        base["professional_avg"] = 0.0
        base["disqualified_count"] = 0
    else:
        grouped = score_df.groupby("work_id", as_index=False).agg(
            judge_count=("judge_id", "nunique"),
            professional_avg=("professional_score", "mean"),
            disqualified_count=("disqualified", lambda s: int(sum(bool(x) for x in s))),
        )
        base = base.merge(grouped, how="left", on="work_id")
        base["judge_count"] = base["judge_count"].fillna(0).astype(int)
        base["professional_avg"] = base["professional_avg"].fillna(0.0)
        base["disqualified_count"] = base["disqualified_count"].fillna(0).astype(int)
    base["professional_weighted"] = base["professional_avg"] * 0.70
    base["network_weighted"] = base["network_vote_score"] * 0.30
    base["final_score"] = base["professional_weighted"] + base["network_weighted"]
    base["status"] = base["disqualified_count"].apply(lambda x: "有取消资格标记" if x else "正常")
    rank_source = base["final_score"].where(base["disqualified_count"] == 0)
    base["rank"] = rank_source.rank(method="min", ascending=False).astype("Int64")
    return base.sort_values(["rank", "final_score"], ascending=[True, False], na_position="last")


def to_excel_bytes(summary, detail):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary.to_excel(writer, index=False, sheet_name="汇总排名")
        detail.to_excel(writer, index=False, sheet_name="评分明细")
    return output.getvalue()


def show_work_card(work):
    st.subheader(f"{work['work_id']}｜{work['team']}——{work['title']}")
    st.write(work.get("description", ""))
    imgs = split_paths(work.get("image_paths", ""))
    if imgs:
        st.markdown("**作品照片**")
        cols = st.columns(2)
        for i, img in enumerate(imgs):
            with cols[i % 2]:
                if img.startswith("http://") or img.startswith("https://") or Path(img).exists():
                    st.image(img, use_container_width=True, caption=f"照片 {i+1}")
                else:
                    st.warning(f"图片未找到：{img}")
    vids = split_paths(work.get("video_paths", ""))
    if vids:
        st.markdown("**作品视频**")
        for i, vid in enumerate(vids):
            if vid.startswith("http://") or vid.startswith("https://") or Path(vid).exists():
                st.video(vid)
            else:
                st.warning(f"视频未找到：{vid}")


def page_judge():
    st.header("教师评分")
    works = load_works()
    data = load_data()
    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("评委姓名", placeholder="如：张老师")
    with col2:
        code = st.text_input("评委编号/手机号后四位（可选，用于区分同名）")
    label = st.selectbox("选择作品", works.apply(lambda r: f"{r['work_id']}｜{r['team']}——{r['title']}", axis=1).tolist())
    if not name.strip():
        st.info("请先填写评委姓名。")
        return
    work_id = label.split("｜")[0]
    work = works[works["work_id"] == work_id].iloc[0].to_dict()
    jid = judge_id(name, code)
    existing = existing_score(data.get("scores", []), jid, work_id) or {}
    show_work_card(work)
    st.divider()

    with st.form("score_form"):
        values = {}
        for group, items in RUBRIC:
            st.markdown(f"### {group}")
            cols = st.columns(len(items))
            for col, (field, label, max_score, help_text) in zip(cols, items):
                with col:
                    values[field] = st.number_input(label + f"（0-{max_score}）", 0, max_score, safe_int(existing.get(field, 0)), 1, help=help_text)
        st.markdown("### 扣分项 / 资格项")
        c1, c2, c3 = st.columns(3)
        with c1:
            common_error = st.checkbox("明显常识性错误（-10）", value=bool(existing.get("common_error", False)))
        with c2:
            no_process = st.checkbox("未提交制作过程佐证材料（-20）", value=bool(existing.get("no_process_evidence", False)))
        with c3:
            disqualified = st.checkbox("抄袭或现成商品模型（取消资格）", value=bool(existing.get("disqualified", False)))
        comment = st.text_area("评语", value=str(existing.get("comment", "")), height=120)
        preview = {**values, "common_error": common_error, "no_process_evidence": no_process}
        st.metric("专业评审原始分", professional_score(preview))
        submitted = st.form_submit_button("提交评分", type="primary")

    if submitted:
        row = {
            "record_id": existing.get("record_id", str(uuid.uuid4())),
            "created_at": existing.get("created_at", now_str()),
            "updated_at": now_str(),
            "judge_id": jid,
            "judge_name": name.strip(),
            "judge_code": code.strip(),
            "work_id": work_id,
            "team": work["team"],
            "title": work["title"],
            **values,
            "common_error": bool(common_error),
            "no_process_evidence": bool(no_process),
            "disqualified": bool(disqualified),
            "comment": comment.strip(),
        }
        scores = data.setdefault("scores", [])
        for i, old in enumerate(scores):
            if old.get("judge_id") == jid and old.get("work_id") == work_id:
                scores[i] = row
                break
        else:
            scores.append(row)
        save_data(data, f"score: {jid} -> {work_id}")
        st.success("已提交。再次提交同一作品会覆盖上一次评分。")
        st.rerun()


def page_admin():
    st.header("管理员后台")
    password = st.text_input("管理员密码", type="password")
    if password != secret("app", "admin_password", "admin123"):
        st.info("请输入管理员密码。")
        return
    works = load_works()
    data = load_data()
    summary = build_summary(data, works)
    detail = scores_dataframe(data)
    tab1, tab2, tab3, tab4 = st.tabs(["汇总排名", "评分明细", "网络投票分", "导出"])
    with tab1:
        cols = ["rank", "work_id", "team", "title", "judge_count", "professional_avg", "professional_weighted", "network_vote_score", "network_weighted", "final_score", "status"]
        st.dataframe(summary[cols].style.format({"professional_avg":"{:.2f}", "professional_weighted":"{:.2f}", "network_vote_score":"{:.2f}", "network_weighted":"{:.2f}", "final_score":"{:.2f}"}), use_container_width=True, hide_index=True)
    with tab2:
        st.dataframe(detail, use_container_width=True, hide_index=True)
    with tab3:
        votes = data.setdefault("votes", {})
        vote_df = works[["work_id", "team", "title"]].copy()
        vote_df["network_vote_score"] = vote_df["work_id"].map(lambda x: float(votes.get(str(x), 0) or 0))
        edited = st.data_editor(vote_df, use_container_width=True, hide_index=True, disabled=["work_id", "team", "title"], column_config={"network_vote_score": st.column_config.NumberColumn("网络投票标准化分", min_value=0.0, max_value=100.0, step=0.1)})
        if st.button("保存网络投票分", type="primary"):
            data["votes"] = {str(r["work_id"]).zfill(2): float(r["network_vote_score"]) for _, r in edited.iterrows()}
            save_data(data, "update network vote scores")
            st.success("已保存。")
            st.rerun()
    with tab4:
        st.download_button("下载汇总排名 CSV", summary.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"), "汇总排名.csv", "text/csv")
        st.download_button("下载评分明细 CSV", detail.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"), "评分明细.csv", "text/csv")
        st.download_button("下载 Excel 总表", to_excel_bytes(summary, detail), "地理模型评分汇总.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.divider()
        confirm = st.text_input("如需清空全部评分，请输入：清空评分")
        if st.button("清空全部评分与投票分"):
            if confirm == "清空评分":
                save_data({"scores": [], "votes": {}}, "reset all scores")
                st.success("已清空。")
                st.rerun()
            else:
                st.error("确认文字不正确，未执行。")


def page_rubric():
    st.header("评分细则")
    st.write("总分 = 网络投票分 × 30% + 专业评审均分 × 70%。")
    for group, items in RUBRIC:
        st.subheader(group)
        st.dataframe(pd.DataFrame([{"评分项": label, "满分": max_score, "说明": help_text} for _, label, max_score, help_text in items]), use_container_width=True, hide_index=True)
    st.subheader("扣分项")
    st.write("- 存在明显常识性错误：-10 分")
    st.write("- 未提交制作过程佐证材料：-20 分")
    st.write("- 使用现成商品模型或存在抄袭：取消参评资格")


def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🌍", layout="wide")
    st.title(APP_TITLE)
    with st.sidebar:
        st.title("评分系统")
        st.caption("专业评审 100 分制；最终按网络投票 30% + 专业评审 70% 汇总。")
        if github_configured():
            st.success("数据存储：GitHub")
            st.caption(secret("github", "repo"))
        else:
            st.warning("数据存储：本地文件。部署后建议配置 GitHub Secrets。")
        page = st.radio("页面", ["教师评分", "管理员后台", "评分细则"])
    try:
        if page == "教师评分":
            page_judge()
        elif page == "管理员后台":
            page_admin()
        else:
            page_rubric()
    except requests.HTTPError as e:
        st.error("GitHub 数据读写失败，请检查 token、repo、branch、scores_path。")
        st.exception(e)
    except Exception as e:
        st.error("系统出现错误。")
        st.exception(e)


if __name__ == "__main__":
    main()
