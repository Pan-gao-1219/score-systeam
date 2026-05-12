"""
地理模型制作大赛 · 观众投票系统
投票流程：填写姓名+手机号 → 短信验证码 → 浏览作品 → 投票
每个手机号仅限投票一次，验证码有效期 5 分钟。
"""
import base64
import json
import random
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
OTP_TTL = 300        # 验证码有效期（秒）
OTP_RESEND_CD = 60   # 重发冷却时间（秒）


# ── 工具函数 ──────────────────────────────────────────────────────────────────

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


# ── 阿里云短信 OTP ────────────────────────────────────────────────────────────

def _aliyun_configured():
    return bool(
        secret("aliyun", "access_key_id")
        and secret("aliyun", "access_key_secret")
        and secret("aliyun", "sign_name")
        and secret("aliyun", "template_code")
    )


def _send_sms_aliyun(phone: str, code: str) -> tuple[bool, str]:
    """调用阿里云短信 API 发送验证码。返回 (成功, 错误信息)。"""
    try:
        from alibabacloud_dysmsapi20170525.client import Client
        from alibabacloud_dysmsapi20170525 import models as sms_models
        from alibabacloud_tea_openapi.models import Config

        cfg = Config(
            access_key_id=secret("aliyun", "access_key_id"),
            access_key_secret=secret("aliyun", "access_key_secret"),
            endpoint="dysmsapi.aliyuncs.com",
        )
        client = Client(cfg)
        req = sms_models.SendSmsRequest(
            phone_numbers=phone,
            sign_name=secret("aliyun", "sign_name"),
            template_code=secret("aliyun", "template_code"),
            template_param=json.dumps({"code": code}, ensure_ascii=False),
        )
        resp = client.send_sms(req)
        if resp.body.code == "OK":
            return True, ""
        return False, f"{resp.body.code}：{resp.body.message}"
    except ImportError:
        return False, "alibabacloud-dysmsapi20170525 未安装，请检查 requirements.txt"
    except Exception as e:
        return False, str(e)


def send_otp(phone: str) -> tuple[bool, str]:
    """生成验证码并发送，存入 session_state。返回 (成功, 错误信息)。"""
    if not _aliyun_configured():
        return False, "未配置阿里云短信 Secrets（access_key_id / access_key_secret / sign_name / template_code）"

    code = str(random.randint(100000, 999999))
    ok, err = _send_sms_aliyun(phone, code)
    if ok:
        st.session_state["otp_code"]      = code
        st.session_state["otp_phone"]     = phone
        st.session_state["otp_expires"]   = time.time() + OTP_TTL
        st.session_state["otp_sent_at"]   = time.time()
        st.session_state["otp_verified"]  = False
    return ok, err


def verify_otp(phone: str, input_code: str) -> tuple[bool, str]:
    """校验用户输入的验证码。返回 (通过, 错误信息)。"""
    if st.session_state.get("otp_phone") != phone:
        return False, "手机号与发送验证码时不一致，请重新发送。"
    if time.time() > st.session_state.get("otp_expires", 0):
        return False, "验证码已过期（有效期 5 分钟），请重新发送。"
    if input_code.strip() != st.session_state.get("otp_code", ""):
        return False, "验证码错误，请重新输入。"
    return True, ""


# ── GitHub 存储 ────────────────────────────────────────────────────────────────

def github_configured():
    return bool(secret("github", "token") and secret("github", "repo"))


def _gh_headers():
    return {
        "Authorization": f"Bearer {secret('github', 'token')}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _gh_url():
    repo   = secret("github", "repo")
    branch = secret("github", "branch", "main")
    return f"https://api.github.com/repos/{repo}/contents/data/public_votes.json", branch


def _gh_load():
    url, branch = _gh_url()
    r = requests.get(url, headers=_gh_headers(), params={"ref": branch}, timeout=20)
    if r.status_code == 404:
        return {"votes": []}, None
    r.raise_for_status()
    payload = r.json()
    raw  = base64.b64decode(payload["content"]).decode("utf-8")
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


# ── 作品展示卡片 ───────────────────────────────────────────────────────────────

def _render_work_cards(works):
    """2 列卡片，展示全部图片 + 视频，返回用户点选的 work_id（或 None）。"""
    if "selected_work_id" not in st.session_state:
        st.session_state.selected_work_id = None

    cols = st.columns(2)
    for idx, (_, work) in enumerate(works.iterrows()):
        wid         = work["work_id"]
        is_selected = st.session_state.selected_work_id == wid
        border      = "#1d72b8" if is_selected else "#d0d0d0"
        bg          = "#eef5ff" if is_selected else "#fafafa"

        with cols[idx % 2]:
            st.markdown(
                f"""<div style="border:2px solid {border};border-radius:10px;
                padding:12px 14px 4px;margin-bottom:6px;background:{bg}">
                <b style="font-size:1.05em">{wid} | {work['team']}</b><br>
                <span style="color:#555;font-size:0.88em">{work['title']}</span>
                </div>""",
                unsafe_allow_html=True,
            )

            # 全部图片（2 列小图）
            imgs = [p for p in split_paths(work.get("image_paths", ""))
                    if p.startswith("http") or Path(p).exists()]
            if imgs:
                ic = st.columns(min(len(imgs), 2))
                for i, p in enumerate(imgs):
                    with ic[i % 2]:
                        st.image(p, use_container_width=True, caption=f"图 {i+1}")

            # 视频折叠
            vids = [v for v in split_paths(work.get("video_paths", ""))
                    if v.startswith("http") or Path(v).exists()]
            if vids:
                with st.expander("▶ 查看视频"):
                    for v in vids:
                        st.video(v)

            label = "✅ 已选择此作品" if is_selected else "投这一票"
            btype = "primary" if is_selected else "secondary"
            if st.button(label, key=f"btn_{wid}", type=btype, use_container_width=True):
                st.session_state.selected_work_id = wid
                st.rerun()
            st.markdown("<div style='margin-bottom:18px'></div>", unsafe_allow_html=True)


# ── 页面：投票（三阶段） ───────────────────────────────────────────────────────

def page_vote():
    # 初始化 session 状态
    for k, v in [("vote_phase", "info"), ("otp_verified", False),
                 ("verified_phone", ""), ("verified_name", ""),
                 ("vote_done", False)]:
        st.session_state.setdefault(k, v)

    # ── 阶段 0：已完成投票 ────────────────────────────────────────────────────
    if st.session_state.vote_done:
        st.success(
            f"**投票已完成！** 感谢 {st.session_state.verified_name} 参与投票。\n\n"
            "每位同学只有一次投票机会，刷新页面不影响结果。"
        )
        st.balloons()
        return

    # ── 阶段 1：填写信息 & 发送验证码 ─────────────────────────────────────────
    if st.session_state.vote_phase == "info":
        st.markdown("### 第 1 步：填写姓名和手机号")
        st.caption("验证码将发送到你填写的手机，用于确认手机号真实有效，每个号码只能投票一次。")

        with st.form("info_form"):
            name  = st.text_input("姓名 *", placeholder="请填写真实姓名")
            phone = st.text_input("手机号 *", placeholder="11 位大陆手机号")
            submitted = st.form_submit_button("发送验证码", type="primary")

        if submitted:
            name, phone = name.strip(), phone.strip()
            if not name:
                st.error("请填写姓名。")
                return
            if not re.match(r"^1[3-9]\d{9}$", phone):
                st.error("请输入正确的 11 位手机号。")
                return

            # 提前检查是否已投过
            existing = {v.get("voter_phone") for v in load_votes().get("votes", [])}
            if phone in existing:
                st.error("该手机号已参与过投票，每个号码仅限一次。")
                return

            ok, err = send_otp(phone)
            if ok:
                st.session_state.vote_phase   = "otp"
                st.session_state.pending_name  = name
                st.session_state.pending_phone = phone
                st.rerun()
            else:
                st.error(f"短信发送失败：{err}")

    # ── 阶段 2：输入验证码 ────────────────────────────────────────────────────
    elif st.session_state.vote_phase == "otp":
        phone = st.session_state.get("pending_phone", "")
        name  = st.session_state.get("pending_name", "")
        masked = phone[:3] + "****" + phone[-4:]

        st.markdown("### 第 2 步：输入验证码")
        st.info(f"验证码已发送至 **{masked}**，请在 5 分钟内填写。")

        with st.form("otp_form"):
            code_input = st.text_input("6 位验证码", max_chars=6, placeholder="请输入验证码")
            col_v, col_r = st.columns([2, 1])
            with col_v:
                verify_btn = st.form_submit_button("验证并继续", type="primary")
            with col_r:
                resend_btn = st.form_submit_button("重新发送")

        if verify_btn:
            ok, err = verify_otp(phone, code_input)
            if ok:
                st.session_state.vote_phase    = "vote"
                st.session_state.verified_phone = phone
                st.session_state.verified_name  = name
                st.session_state.otp_verified   = True
                st.rerun()
            else:
                st.error(err)

        if resend_btn:
            last_sent = st.session_state.get("otp_sent_at", 0)
            cd = OTP_RESEND_CD - int(time.time() - last_sent)
            if cd > 0:
                st.warning(f"请等待 {cd} 秒后再重新发送。")
            else:
                ok, err = send_otp(phone)
                if ok:
                    st.success("验证码已重新发送。")
                else:
                    st.error(f"发送失败：{err}")

        if st.button("← 返回修改手机号"):
            st.session_state.vote_phase = "info"
            st.rerun()

    # ── 阶段 3：浏览作品 & 投票 ───────────────────────────────────────────────
    elif st.session_state.vote_phase == "vote":
        phone = st.session_state.verified_phone
        name  = st.session_state.verified_name
        st.success(f"手机号已验证：**{phone[:3]}****{phone[-4:]}**　姓名：**{name}**")

        st.divider()
        st.markdown("### 第 3 步：浏览作品，选出你最喜欢的一件")

        works = load_works()
        _render_work_cards(works)

        selected_id = st.session_state.selected_work_id
        if selected_id:
            sel = works[works["work_id"] == selected_id].iloc[0]
            st.success(f"你选择了：**{selected_id} | {sel['team']}——{sel['title']}**")

        st.divider()
        if st.button("确认提交投票", type="primary", disabled=not selected_id):
            # 二次防重（防止并发重复提交）
            data     = load_votes()
            existing = {v.get("voter_phone") for v in data["votes"]}
            if phone in existing:
                st.error("该手机号已投过票。")
                return

            work = works[works["work_id"] == selected_id].iloc[0]
            record = {
                "vote_id":     str(uuid.uuid4()),
                "voted_at":    now_str(),
                "voter_name":  name,
                "voter_phone": phone,
                "work_id":     selected_id,
                "team":        work["team"],
                "title":       work["title"],
            }
            data["votes"].append(record)
            save_votes(data, f"vote: {phone[:7]}*** -> {selected_id}")

            st.session_state.vote_done = True
            st.rerun()


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

    data  = load_votes()
    votes = data.get("votes", [])
    st.metric("当前总投票数", len(votes))

    if not votes:
        st.warning("暂无投票记录。")
        return

    raw_df = pd.DataFrame(votes)

    stat_df = (
        raw_df.groupby(["work_id", "team", "title"], as_index=False)
        .size().rename(columns={"size": "票数"})
        .sort_values("票数", ascending=False).reset_index(drop=True)
    )
    stat_df.index += 1

    detail_export = raw_df[["voted_at", "voter_name", "voter_phone",
                             "work_id", "team", "title"]].copy()
    detail_export.columns = ["投票时间", "姓名", "手机号", "作品编号", "队伍", "作品名"]
    detail_display = detail_export.copy()
    detail_display["手机号"] = detail_display["手机号"].apply(
        lambda p: p[:3] + "****" + p[-4:] if len(str(p)) == 11 else p
    )

    tab1, tab2, tab3 = st.tabs(["投票统计", "投票明细", "导出 & 管理"])

    with tab1:
        st.dataframe(stat_df, use_container_width=True)

    with tab2:
        st.caption("手机号已脱敏（前 3 后 4），导出文件含完整号码。")
        st.dataframe(detail_display, use_container_width=True, hide_index=True)

    with tab3:
        st.download_button(
            "下载投票明细 CSV（含完整手机号）",
            detail_export.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
            "投票明细.csv", "text/csv",
        )
        st.download_button(
            "下载投票统计 CSV",
            stat_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
            "投票统计.csv", "text/csv",
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
                st.success("已清空。")
                st.rerun()
            else:
                st.error("确认文字不正确，未执行。")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🗳️", layout="wide")
    st.title(APP_TITLE)

    with st.sidebar:
        st.title("导航")
        st.caption("验证手机号 → 浏览作品 → 投票")
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
