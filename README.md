# 每日医学科研精读日报 · 云端自动生成

每天北京时间 **07:00** 自动在 GitHub Actions(免费云端)生成当日医学科研精读日报:
抓取 **PubMed** 最新医学论文 + **arXiv** 最新 AI 论文 → 生成 Markdown + PDF → 自动提交到仓库。
全程与你电脑开不开机无关。

## 项目结构

```
daily-report-auto/
├── .github/workflows/daily-report.yml   # 云端定时任务定义(每日 07:00)
├── scripts/
│   ├── generate_report.py               # 抓取 + 生成脚本(零第三方依赖)
│   ├── classics.json                    # 「每日经典精读」文献库,可自行增删
│   ├── report.css                       # PDF 版式(中文友好)
│   └── requirements.txt                 # 零依赖说明
└── reports/                             # 日报输出(自动生成,提交到仓库)
```

## 如何部署(一次性,约 5 分钟)

1. **在 GitHub 上新建一个仓库**(Public 或 Private 都可以),例如 `daily-med-report`。不要勾选 README 初始化。
2. 本地把本目录推上去:

   ```bash
   cd daily-report-auto
   git init
   git add .
   git commit -m "init: 每日医学科研精读日报自动化"
   git branch -M main
   git remote add origin https://github.com/<你的用户名>/daily-med-report.git
   git push -u origin main
   ```

3. 打开仓库页面 → **Actions** 标签页,能看到 `每日医学科研精读日报` 工作流。
4. **立即验证一次**(不必等到明天):Actions 页面左侧点该工作流 → 右侧 **Run workflow** 按钮 → 直接点绿色按钮运行。
5. 跑完后刷新仓库,`reports/` 目录下就会出现 `YYYYMMDD_医学科研精读日报.md` 和 `.pdf`。
   之后每天 07:00(北京时间)自动运行。

## 如何调整内容

| 想改什么 | 改哪里 |
|---|---|
| PubMed 关注方向/数量 | `scripts/generate_report.py` 顶部 `PUBMED_QUERIES` / `PUBMED_PER_QUERY` / `PUBMED_MAX` |
| arXiv 分类/数量 | `ARXIV_CATEGORIES` / `ARXIV_PER_CATEGORY` / `ARXIV_MAX` |
| 经典精读文章 | 编辑 `scripts/classics.json`(每天按日期自动轮换一篇) |
| 运行时间 | `.github/workflows/daily-report.yml` 里的 `cron`(UTC 时间,北京时间减 8 小时) |
| 通知(邮件/Telegram/企业微信) | 在 workflow 末尾追加推送步骤,README 见下方示例 |

改完 `git push` 即可,云端会自动用新配置。

## 通知推送示例(可选)

在 `daily-report.yml` 的 `提交并推送日报` 步骤后追加(邮件最简):

```yaml
      - name: 发送邮件通知
        uses: dawidd6/action-send-mail@v3
        with:
          server_address: smtp.qq.com
          server_port: 465
          username: ${{ secrets.MAIL_USER }}
          password: ${{ secrets.MAIL_PASS }}
          subject: 医学科研精读日报 ${{ github.event.repository.updated_at }}
          to: 你的邮箱@example.com
          from: ${{ secrets.MAIL_USER }}
          body: 见仓库 reports/ 目录
```

然后在仓库 **Settings → Secrets and variables → Actions** 里添加 `MAIL_USER` / `MAIL_PASS`。

## 常见问题

- **为什么偶尔不是准点 7 点?** GitHub Actions 的定时任务有排队延迟(官方说明可能延迟数分钟),属正常现象。
- **某天某板块为空?** 数据源当天无新收录或 API 临时波动,脚本会自动跳过并照常出日报,不影响其余内容。
- **想暂停?** Actions 页面右上角关闭该工作流即可,想恢复再打开。
- **想清理历史日报?** 直接删 `reports/` 里旧文件并提交即可。

## 数据源

- PubMed E-utilities(公开免费,无需密钥)
- arXiv API(公开免费)
- 微信搜一搜无公开 API,「科研公众号干货」板块为占位说明,保留原样。
