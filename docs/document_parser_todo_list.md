# 📋 文件解析模块待更新清单

> 基于对 **RAGFlow** 和 **LightRAG** 项目的文件解析模块分析，结合当前项目的实现，总结以下可改进的方向。

---

## 🔴 第一阶段：高优先级（建议立即实施）

| 序号 | 功能模块 | 具体任务 | 技术要点 | 参考文件 |
|:---:|:--------|:--------|:--------|:--------|
| 1 | **Excel 解析器** | 创建 `excel_parser.py` | • 支持 .xlsx/.xls 格式<br>• 多工作表处理<br>• 图片提取功能 | `ragflow/deepdoc/parser/excel_parser.py` |
| 2 | **PDF 乱码检测** | 增强 `docling_parser.py` | • CID 模式检测<br>• 字体子集前缀检测<br>• 自动 OCR 回退 | `ragflow/deepdoc/parser/pdf_parser.py`<br>方法：`_is_garbled_text` |
| 3 | **本地 OCR 集成** | 创建 `paddle_ocr.py` | • PaddleOCR 封装<br>• GPU/CPU 自适应<br>• ONNX Runtime 加速 | `ragflow/deepdoc/vision/ocr.py` |

---

## 🟡 第二阶段：中优先级（建议 2-4 周内完成）

| 序号 | 功能模块 | 具体任务 | 技术要点 | 参考文件 |
|:---:|:--------|:--------|:--------|:--------|
| 4 | **版面分析** | 创建 `layout_analyzer.py` | • 布局元素识别（Text/Title/Table/Figure）<br>• 页眉页脚过滤<br>• YOLOv10/LayoutLMv3 模型 | `ragflow/deepdoc/vision/layout_recognizer.py` |
| 5 | **表格结构识别** | 创建 `table_recognizer.py` | • 行列关系重建<br>• 合并单元格处理<br>• 表格方向校正<br>• HTML/Markdown 输出 | `ragflow/deepdoc/vision/table_structure_recognizer.py` |
| 6 | **EPUB 支持** | 创建 `epub_parser.py` | • Spine 阅读顺序解析<br>• XHTML 内容提取<br>• 元数据提取 | `ragflow/deepdoc/parser/epub_parser.py` |
| 7 | **HTML 支持** | 创建 `html_parser.py` | • BeautifulSoup 解析<br>• 标签过滤（script/style）<br>• 表格分块处理 | `ragflow/deepdoc/parser/html_parser.py` |
| 8 | **Markdown 支持** | 创建 `markdown_parser.py` | • 表格提取<br>• 代码块保留<br>• 标题层次识别 | `ragflow/deepdoc/parser/markdown_parser.py` |

---

## 🟢 第三阶段：低优先级（建议 1-2 个月内完成）

| 序号 | 功能模块 | 具体任务 | 技术要点 | 参考文件 |
|:---:|:--------|:--------|:--------|:--------|
| 9 | **文档旋转校正** | 增强 PDF 解析器 | • 页面方向检测（0°/90°/180°/270°）<br>• OCR 置信度评估<br>• 自动旋转校正 | `ragflow/deepdoc/parser/pdf_parser.py`<br>方法：`_evaluate_table_orientation` |
| 10 | **图片自动描述** | 增强 OCR 模块 | • 多模态模型集成（GLM-4V）<br>• 图片描述生成<br>• 图文关联存储 | `ragflow/deepdoc/parser/excel_parser.py`<br>图片提取部分 |
| 11 | **纯文本 (TXT) 支持** | 创建 `txt_parser.py` | • 编码自动检测（chardet）<br>• 大文件分块读取 | `ragflow/deepdoc/parser/txt_parser.py` |
| 12 | **简历解析** | 创建 `resume/` 模块 | • 实体识别（学校、公司、技能）<br>• 结构化信息提取 | `ragflow/deepdoc/parser/resume/` |

---

## 🔧 工程优化任务（贯穿各阶段）

| 序号 | 优化项 | 具体任务 | 技术要点 |
|:---:|:------|:--------|:--------|
| 13 | **位置信息保留** | 增强所有解析器 | • 页码/坐标信息记录<br>• 位置标签格式：`@@{page}\t{x0}\t{x1}\t{top}\t{bottom}##` |
| 14 | **语义分块** | 优化 `document_segmentation.py` | • XGBoost 文本块合并判断<br>• 布局特征提取<br>• 多栏文档排序 |
| 15 | **并行处理** | 架构优化 | • 多页并行 OCR<br>• 异步处理<br>• 进度回调 |
| 16 | **内存管理** | 性能优化 | • 大文件分块处理<br>• GPU 内存释放<br>• 模型缓存机制 |

---

## 📁 建议文件结构

```
app/document_parser/
├── __init__.py                    # 统一导出接口
├── base_parser.py                 # 基础解析器抽象类
├── docling_parser.py              # 【已有】Docling 封装
├── glm_ocr_client.py              # 【已有】智谱 OCR
├── paddle_ocr.py                  # 【新增】本地 OCR
├── document_segmentation.py       # 【已有】文档分块
├── layout_analyzer.py             # 【新增】版面分析
├── table_recognizer.py            # 【新增】表格识别
├── excel_parser.py                # 【新增】Excel 解析
├── epub_parser.py                 # 【新增】EPUB 解析
├── html_parser.py                 # 【新增】HTML 解析
├── markdown_parser.py             # 【新增】Markdown 解析
├── txt_parser.py                  # 【新增】文本解析
└── resume/                        # 【新增】简历解析
    ├── __init__.py
    ├── step_one.py
    ├── step_two.py
    └── entities/
        ├── __init__.py
        ├── schools.py
        ├── corporations.py
        └── ...
```

---

## 📊 实施建议

### 1. 先做 Excel 解析器
- 业务需求最迫切
- 实现相对简单
- 使用 `openpyxl` + `pandas` 即可快速完成

### 2. PDF 乱码检测
- 提升现有解析质量
- 用户体验明显改善
- 参考 RAGFlow 的 `_is_garbled_text` 实现

### 3. 本地 OCR 作为备选
- 降低对云端 API 的依赖
- 提高稳定性
- PaddleOCR 或 RapidOCR 均可

### 4. 版面分析和表格识别
- 可以一起实现
- 共享底层模型基础设施
- 需要 GPU 资源支持

---

## 🔗 参考资源

### RAGFlow 解析模块
```
ragflow/deepdoc/parser/
├── __init__.py
├── pdf_parser.py          # PDF 解析（版面分析、表格识别）
├── docx_parser.py         # Word 解析
├── excel_parser.py        # Excel 解析
├── ppt_parser.py          # PPT 解析
├── epub_parser.py         # EPUB 解析
├── html_parser.py         # HTML 解析
├── markdown_parser.py     # Markdown 解析
├── txt_parser.py          # 文本解析
├── docling_parser.py      # Docling 集成
└── resume/                # 简历解析

ragflow/deepdoc/vision/
├── ocr.py                 # OCR 引擎
├── layout_recognizer.py   # 版面识别
├── table_structure_recognizer.py  # 表格结构识别
└── recognizer.py          # 基础识别器
```

### 当前项目结构
```
app/document_parser/
├── docling_parser.py      # Docling 封装
├── glm_ocr_client.py      # 智谱 OCR
└── document_segmentation.py  # 文档分块
```

---

## 🛠️ 技术选型建议

| 功能 | 推荐方案 | 备选方案 |
|:-----|:--------|:--------|
| PDF 解析 | Docling + 自定义后处理 | pdfplumber + 版面分析 |
| OCR | PaddleOCR (本地) | RapidOCR / GLM-4V (云端) |
| 版面分析 | LayoutLMv3 / YOLOv10 | 规则-based |
| 表格识别 | Table Transformer | 规则-based |
| Excel | openpyxl + pandas | xlrd |
| EPUB | ebooklib | 自定义解析 |

---

*文档生成时间：2026-03-26*
