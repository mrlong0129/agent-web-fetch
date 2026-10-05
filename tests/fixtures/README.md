# Test fixtures

No third-party article text is stored here.

- `wechat_text_post.html`: trimmed copy of a real mp.weixin.qq.com "text-only" post page (`item_show_type=10`) as served to the iPhone WeChat UA in 2026-10. The page structure the extractor relies on is kept (`og:title`, `content_noencode`, `window.msg_title`, `nick_name`, `author`, `create_time`, decoy assignments); all article text, account/author names and `__biz` / `gh_` / `sn` IDs are original placeholders, and the ~2 MB of bundled JS was removed.
- `wechat_verify.html`: real WeChat "环境异常" verification page returned to a non-WeChat client, with captcha/session tokens redacted (`REDACTED`).
- Everything else is synthetic: `normal_article.html`, `jsonld_article.html`, `nextjs_page.html`, `cloudflare_challenge.html`, `login_wall.html`, `paywall.html`, `soft_404.html`, `thin_with_feed.html` + `feed.xml`.
