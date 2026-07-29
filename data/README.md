# 本地数据说明

`data/` 用于存放本地领域资料、图谱元数据和评估数据。除明确标注可再分发的项目自有样例外，原始医学文献和数据不应提交到 Git。

## 仓库内文件

- `kg_schema_metadata.json`：项目生成的图谱 schema 元数据，用于开发和结构诊断。

## 第三方医学资料

开发过程中可以在本地使用《中国高血压防治指南（2024 年修订版）》等公开资料，但公开可访问不等于允许随代码仓库再次分发全文。请从出版方或开放获取页面自行获取，并遵守对应页面的版权和使用条款：

- [中华高血压杂志（中英文）2024 年第 32 卷第 7 期](https://cjournal.hep.com.cn/1673-7245/CN/2024/32/07)
- [PubMed Central：Chinese Guidelines for the Prevention and Treatment of Hypertension (2024 revision)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11937835/)

第三方资料、模型和数据不因被本项目读取而适用仓库的 Apache-2.0 许可证。

## 本地目录建议

```text
data/
├─ raw/          # 原始资料，不提交
├─ processed/    # 解析或切分产物，不提交
└─ private/      # 私有评估数据，不提交
```

模型路径由 `.env` 中的 `EMBEDDING_MODEL_PATH` 和 `RERANKER_MODEL_PATH` 指定。请勿提交模型权重、运行缓存、真实患者数据、带授权限制的数据集或包含个人信息的日志。
