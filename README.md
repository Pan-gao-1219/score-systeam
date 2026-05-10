# 地理模型制作大赛评分系统（含照片和视频版）

这是一个基于 Streamlit 的多评委评分网站，已从 Word 材料中提取并加入作品照片和嵌入视频。

## 已包含的媒体

- 作品照片：18 个作品的全部展示照片，位于 `assets/images/`
- 嵌入视频：
  - 01 3A小队：`assets/videos/work_01_video.mp4`
  - 17 6组：`assets/videos/work_17_video.mp4`
- Word 原始媒体备份：`assets/original_media/`

## 功能

- 多位老师分别打分
- 同一老师对同一作品再次提交会覆盖旧评分
- 教师评分页可查看作品简介、照片和视频
- 管理员后台可查看汇总排名、评分明细、录入网络投票标准化分
- 自动计算：专业评审均分、专业评审 70% 折算分、网络投票 30% 折算分、最终总分、排名
- 支持导出 CSV / Excel

## 本地运行

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## 部署到 Streamlit Cloud

- Repository: `Pan-gao-1219/score-systeam`
- Branch: `main`
- Main file path: `streamlit_app.py`

在 Streamlit Cloud 的 Secrets 中填写：

```toml
[github]
token = "github_pat_xxxxxxxxxxxxxxxxx"
repo = "Pan-gao-1219/score-systeam"
branch = "main"
scores_path = "data/scores.json"

[app]
admin_password = "你自己的后台密码"
```

GitHub Token 建议使用 fine-grained token，只授权这个仓库，并给 `Contents: Read and write` 权限。

## 默认管理员密码

没有配置 Secrets 时，默认管理员密码是：

```text
admin123
```

正式使用一定要修改。
