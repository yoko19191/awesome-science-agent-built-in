# OpenBioMed Runtime Environment

本文档详细描述了运行 `openbiomed/skills` 中 45 个生物医药技能所需的计算环境、系统级依赖、硬件加速要求、模型预训练权重目录与环境变量配置。

---

## 1. 语言环境与硬件加速要求

- **Python 运行时**: `Python >= 3.9, <= 3.10`（由于 PyTorch 2.0.1、PyTorch Geometric 扩展库、OpenMM 与 RDKit 在底层 C++/CUDA ABI 绑定的要求，强烈建议采用 Python 3.10）。
- **GPU 与 CUDA 加速**:
  - **CUDA 版本**: `CUDA 11.8` 或 `CUDA 12.1`。
  - **显存要求**:
    - 大分子结构预测与全原子扩散（Boltz-2, BoltzGen, tFold, ESMFold）：建议单卡显存 `>= 24GB`（如 RTX 3090/4090 或 A100/H100）。
    - 单细胞大模型（scGPT, Geneformer, LangCell, SToFM）：微调与大规模细胞嵌入提取建议显存 `>= 32GB`。
    - 快速性质预测（GraphMVP, Prodigy, BioT5 推理）：`>= 8GB`。

---

## 2. 外部生物信息学与化学 CLI 工具

部分技能涉及原始测序比对、质谱解析与结构渲染，需预先在系统 PATH 中安装以下二进制工具链：

### (1) ATAC-seq 染色质可及性分析流水线
- `bowtie2`: 测序 Reads 比对至参考基因组 (hg38/mm10)。
- `samtools`: BAM 文件排序、去重与索引处理。
- `macs2`: 染色质开放区域峰值识别 (Peak Calling)。
- `bedtools`: 峰值集合并、区间交集与一致性峰集构建。
- `fastp`: 原始 FASTQ 接头剪切与质量控制。

### (2) 蛋白质质谱组学流水线
- `comet`: 质谱 MS2 二级谱图与蛋白质序列数据库搜索引擎。
- `msconvert` (ProteoWizard): 将原始仪器格式 (RAW, WIFF) 转换为通用 `mzML`。
- `pyopenms`: 谱图质心化、同位素簇提取与特征检测。

### (3) 分子建模与逆合成规划
- `PyMOL`: 用于大分子复合物结构对齐、表面渲染与静电势分析。
- `PLIP`: 蛋白-配体相互作用图谱解析工具。
- `AiZynthFinder`: 树状蒙特卡洛 (MCTS) 逆合成路径搜索引擎。

---

## 3. 环境变量与 API 访问配置

在系统环境变量或 `.env` 文件中配置以下变量：

```bash
# 1. 实时文献与疾病情报网络检索 (用于 disease-drug-intelligence)
export TAVILY_API_KEY="tvly-your_tavily_api_key"

# 2. 逆合成反应模板库与商业原料分子库路径 (用于 retrosynthesis-planning)
export AIZYNTH_DATA_DIR="/path/to/aizynthfinder_rules_and_stock"

# 3. NCBI 文献查询加速凭据 (用于 biomedical-literature-search)
export NCBI_API_KEY="your_ncbi_api_key"
export NCBI_EMAIL="your_email@domain.com"

# 4. OpenBioMed 本地模型推理服务网关 (用于通过 HTTP 调度后端模型)
export OPENBIOMED_BASE_URL="http://127.0.0.1:8000"

# 5. PyMOL 无头渲染配置
export PYOPENGL_PLATFORM="osmesa"
```

---

## 4. 本地基础大模型权重路径配置

技能库中部分单细胞与空间转录组基础大模型支持本地挂载权重（如在高性能计算节点 `/DATA/disk0/` 存储盘）：

- **scGPT**: `/DATA/disk0/zhaosy/home/scGPT`（包含 `args.json`, `vocab.json`, `best_model.pt`）
- **LangCell**: `/DATA/disk0/zhaosy/home/LangCell`（包含 LangCell-CE 细胞编码器与候选文本描述库）
- **SToFM**: `/DATA/disk0/zhaosy/home/SToFM`（包含 Cell Encoder 与 SE(2) Transformer 空间几何编码权重）
