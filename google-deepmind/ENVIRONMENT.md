# Google DeepMind Science Skills Runtime Environment

本文档详细描述了运行 `google-deepmind/science-skills` 所需的系统环境、运行时包管理器、第三方外部工具与环境变量配置。

---

## 1. 语言与运行时要求

- **Python**: `>= 3.10`（部分前沿模块推荐 Python 3.11 - 3.14，例如 `alphagenome-atlas-website-links` 和 `alphagenome-variant-impact-score` 在 pyproject 中定义推荐 `>=3.14`，常规环境采用 Python 3.11+ 搭配 uv 即可无缝运行）。
- **Node.js & npx**: `>= 18.0.0`（用于通过 `npx skills add google-deepmind/science-skills/` 进行技能包的分发与安装）。

---

## 2. 核心包管理器与运行工具

- **`uv` / `uvx` (Astral)**:
  - Google DeepMind Science Skills 核心依赖 Astral `uv` 进行零配置隔离执行。各技能中的 Python 脚本均由 `uv run` 或 PEP 0723 内联元数据直接驱动。
  - **安装方式**:
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # 或在 macOS 上使用 Homebrew:
    brew install uv
    ```
  - **验证命令**:
    ```bash
    uv --version
    uvx --version
    ```

---

## 3. 结构生物学与无头渲染环境

- **PyMOL (Headless / OSMesa)**:
  - 结构可视化技能（`pymol`）通过 `uv` 自动拉取 `pymol-open-source-whl` 并使用 OSMesa（Off-Screen Mesa）进行纯软件渲染，无需独立 GPU、显示器或 X11 Server 支持。
  - **无头环境变量要求**:
    ```bash
    export PYOPENGL_PLATFORM=osmesa
    ```
  - 支持直接将三维大分子结构（PDB / mmCIF）保存为高分辨率 PNG 图像并同步导出 `.pse` (PyMOL Session) 会话文件。

---

## 4. 环境变量与 API 访问凭据配置

虽然多数公共科研数据库（如 UniProt、Ensembl、PubChem、PubMed 等）提供公开 REST API，但针对特定深度学习模型与高频查询服务，需要配置相应的 API 密钥：

在项目根目录或用户主目录的 `~/.env` 文件中配置以下变量：

```bash
# 1. Google DeepMind AlphaGenome 服务凭证 (必需，用于运行变异效应预测与 AVI 评分)
ALPHAGENOME_API_KEY="your_alphagenome_api_key_here"

# (可选) GENCODE v46 GTF 本地注释文件绝对路径 (加速外显子与剪接坐标解析)
ALPHAGENOME_GTF_PATH="/path/to/gencode.v46.annotation.gtf"

# 2. NCBI E-utilities 凭证 (推荐配置，将请求频率限制从 3 req/s 提升至 10 req/s)
NCBI_API_KEY="your_ncbi_api_key"
NCBI_EMAIL="your_email@domain.com"
NCBI_TOOL="deepmind-science-skills"

# 3. OpenAlex 学术图谱凭证 (推荐配置，享有高级速率限制与礼貌池保障)
OPENALEX_API_KEY="your_openalex_api_key"
USER_EMAIL="your_email@domain.com"

# 4. openFDA 接口凭证 (可选，获得更高 QPM 额度)
FDA_API_KEY="your_openfda_api_key"
```

---

## 5. 开源许可与合规条款要求

根据 `SKILL_LICENSES.md` 规定，在使用特定专有模型与数据集时，系统将在根目录下初始化合规许可凭据记录文件：
- **Predicting The Past (Aeneas / Ithaca)**: `.licenses/predictingthepast_LICENSE.txt`
- **PyMOL**: `.licenses/pymol_LICENSE.txt`
