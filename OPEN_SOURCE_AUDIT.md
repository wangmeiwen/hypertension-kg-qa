# 开源发布审计

审计日期：2026-07-30

## 审计范围

本次审计覆盖：

- GitHub 社区健康文件和仓库元数据；
- README、安装说明、配置模板和测试命令；
- 许可证与第三方数据边界；
- Git 跟踪范围、密钥风险和生成产物；
- CI、安全工作流和公开漏洞报告入口。

审计只判断当前仓库是否具备公开协作和工程发布的基本条件，不构成法律意见、医学质量认证或生产安全认证。

## 审计结论

仓库已有 Apache License 2.0、README、后端/前端 CI 和独立安全工作流，工程说明与实际技术栈基本一致。审计时 GitHub Community Profile 健康度为 42%，主要缺口是贡献指南、行为准则、Issue/PR 模板和安全政策。

本轮补齐：

- `CONTRIBUTING.md`：开发环境、测试矩阵、架构边界和 PR 要求；
- `CODE_OF_CONDUCT.md`：社区行为与执行规则；
- `SECURITY.md`：支持范围、私有报告流程和安全问题边界；
- `SUPPORT.md`：使用支持、医疗免责声明和隐私要求；
- `.github/ISSUE_TEMPLATE/`：缺陷与功能建议表单；
- `.github/PULL_REQUEST_TEMPLATE.md`：验证、安全和数据来源检查清单；
- `data/README.md`：第三方医学资料的获取方式与授权边界；
- README：CI 状态、预发布状态、治理文档入口和数据分发说明。

## 数据与仓库卫生

审计发现当前 Git 树跟踪了两份第三方医学指南 PDF 和一个运行缓存压缩文件。项目代码不依赖这些具体文件名，本轮已将它们从当前 Git 跟踪范围移除，同时保留本地文件，并通过 `.gitignore` 防止再次加入。

需要注意：

- 删除提交不能移除历史提交中的已有对象；
- 如果权利人要求从所有历史中彻底删除，需要单独评估并执行历史重写；
- 历史重写会改变提交哈希并影响现有克隆和分支，不应作为普通文档提交的一部分；
- 新增第三方全文、数据集或模型前，必须记录来源、版本、许可证和再分发依据。

## 安全与隐私

- `.env` 未被 Git 跟踪，公开配置使用 `.env.example` 占位值；
- 仓库已有 Python、前端依赖审计、`detect-secrets` 和 Trivy 容器扫描；
- 安全报告应使用 GitHub 私有漏洞报告，不在公开 issue 披露；
- 患者信息、真实问答日志、数据库快照、API key 和私有评估数据不得提交。

GitHub 仓库设置在审计时尚未启用私有漏洞报告、依赖漏洞提醒、Dependabot 安全更新、Secret scanning 和默认分支保护。本轮已启用私有漏洞报告、依赖漏洞提醒和 Dependabot 安全更新，使 `SECURITY.md` 的私有入口真实可用。Secret scanning 和默认分支保护仍未启用；这些设置应结合 GitHub 账户能力和维护流程另行评估，避免在没有合并规则设计时直接改变协作流程。

## 仍需维护者持续负责的事项

1. 发布稳定版本前建立版本号、变更日志和升级策略。
2. 在合并前确认新加入依赖的许可证与分发方式兼容。
3. 对用于评估或演示的医学数据保留来源、授权和脱敏记录。
4. 将工程测试、真实数据库验证、模型效果和医学质量验收分别记录。
5. 根据协作规模配置默认分支保护、必需检查和维护者审批。
6. 定期复查安全例外和容器版本，不让临时忽略永久化。

## 参考

- [GitHub：About community profiles for public repositories](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/about-community-profiles-for-public-repositories)
- [GitHub：Adding a security policy to your repository](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/add-security-policy)
- [Contributor Covenant 2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/)
