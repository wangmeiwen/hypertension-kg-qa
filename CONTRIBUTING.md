# 贡献指南

感谢你关注 HypertensionKG QA System。项目仍处于研究与工程验证阶段，欢迎提交缺陷修复、测试、文档、性能改进和可验证的检索或图谱优化。

## 开始之前

- 阅读 [README.md](./README.md) 了解运行方式和能力边界。
- 阅读 [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md) 并遵守社区行为准则。
- 安全漏洞请按照 [SECURITY.md](./SECURITY.md) 私下报告，不要创建公开 issue。
- 不要在 issue、日志、测试样例或提交中包含患者信息、真实密钥、数据库口令和未获授权的医学资料。

## 开发环境

项目主要在 Python 3.11 和 Node.js 20.19+ 环境下验证。后端推荐使用 Conda：

```powershell
conda activate hypertension-qa
pip install -r requirements.txt
```

前端依赖使用仓库内的锁文件安装：

```powershell
cd app/frontend
npm ci
cd ../..
```

复制配置模板后再填写本地值：

```powershell
Copy-Item .env.example .env
```

`.env` 只用于本地运行，禁止提交。

## 提交变更

1. 从最新的 `main` 创建短生命周期分支。
2. 保持一次变更只解决一个明确问题。
3. 先复现缺陷，再提交修复；行为变化需要对应测试。
4. 遵循现有的 `router -> service -> domain/repository -> external runtime` 边界，不在路由或 UI 组件中重新堆积业务逻辑。
5. 不提交生成产物、缓存、日志、模型权重、大型数据集或第三方全文资料。
6. 使用清晰、简短的提交信息说明变更目的。

## 验证

后端变更至少运行相关测试，并尽量完成：

```powershell
python -m pytest tests/automated -m "not db"
ruff check app tests
black --check app tests
mypy app
```

涉及 MySQL、Neo4j 或 Milvus 的变更还应在真实服务可用时运行：

```powershell
python -m pytest tests/automated -m db
```

前端变更至少运行：

```powershell
cd app/frontend
npm run format:check
npm run lint
npm run typecheck
npm run stylelint
npm run test:coverage
npm run build
```

涉及用户主流程时还应运行：

```powershell
npm run test:e2e
```

如果某项检查无法运行，请在 pull request 中明确说明原因，不要把未运行写成已通过。

## Pull Request 要求

Pull request 应包含：

- 变更动机和问题背景；
- 主要实现与影响范围；
- 已执行的验证命令和结果；
- 未验证的真实依赖、模型效果或医学质量边界；
- 涉及界面时的截图，涉及接口时的兼容性说明；
- 关联的 issue（如有）。

维护者可能要求拆分过大的变更、补充测试或说明数据来源。提交贡献即表示你同意该贡献按照仓库的 [Apache License 2.0](./LICENSE) 发布，并确认自己有权提交相关内容。
