# Google Antigravity Science Plugin 运行环境

本文描述运行 `google-antigravity/science`（插件名 `science`，`plugin.json` 版本 1.4.0）所需的宿主、包管理器、外部程序和凭据。技能脚本不共用一个虚拟环境，由 Astral `uv` 按每个脚本的 PEP 723 元数据单独安装依赖。

相对 `google-deepmind/science-skills`，本插件多 8 个技能：`rdkit_general`、`cheminformatics_core`、`cheminformatics_doc2mol`、`cheminformatics_ml`、`cheminformatics_physics`、`cheminformatics_reactions`、`cheminformatics_cofolding`、`biomolecular_structure_viewer`。其余 40 个技能的脚本与 DeepMind 树一致，`SKILL.md` 仅增加 `metadata.publisher: google`。

---

## 1. 语言与宿主

- **Python**：按技能拆开，不能收成一个解释器。
  - 数据库与文献类脚本：`>=3.10`。
  - `cheminformatics_core`、`cheminformatics_doc2mol`、`cheminformatics_reactions` 的部分脚本：`>=3.11`。
  - 本地 Boltz-2：`uvx --python 3.11 --from boltz`。
  - `alphagenome_atlas_website_links`、`alphagenome_variant_impact_score` 的 `pyproject.toml`：`>=3.14`。
  - PyMOL 示例脚本：`>=3.10, <3.13`（`pymol-open-source-whl`）。
- **Node.js**：结构查看器不从本仓库 `npm install`。`sidecars/biomolecular_structure_viewer/sidecar.json` 用 `node dist/server.cjs` 起 UI；`mcp_config.json` 用宿主命令 `agy-node` 起 `sidecars/biomolecular_structure_viewer/dist/mcp_entry.cjs`。二者都是 Antigravity 宿主提供的运行时，sidecar 只带已构建的 `dist/`。
- **Antigravity 会话目录**：查看器只加载当前对话 artifact 目录里的本地 `.cif` / `.mmcif` / `.pdb`。数据库 ID 和 URL 会被 `load_structure` 拒绝，需先下载并 `gunzip`。

---

## 2. 包管理器

- **`uv` / `uvx`**：所有 Python 技能的前置。技能 `uv` 的安装步骤是：
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  uv --version
  ```
  Windows 使用 `irm https://astral.sh/uv/install.ps1 | iex`。
- 执行形式是 `uv run /path/to/script.py`。不要写成 `uv run python3 script.py`，否则会跳过脚本头部的依赖块。
- README 约定密钥写在 `~/.env`。首次触发技能时，宿主可以提示安装 `uv`，装完后建议重启 Antigravity。
- 自定义技能不要改插件安装目录，放到 `~/.gemini/config/skills/`。

---

## 3. 结构查看、无头渲染与化学外部程序

- **PyMOL（OSMesa）**：`pymol` 技能在脚本里设置 `PYOPENGL_PLATFORM=osmesa`，用 `cmd.png()` 出图并保存 `.pse`。不使用 GPU、显示器或 X11，也不要调用硬件 `cmd.ray()` / `cmd.draw()`。交互式查看优先走 Biomolecular Structure Viewer（Mol* sidecar），PyMOL 只作静态渲染和测量。
- **Mol* sidecar**：MCP 服务名 `biomolecular_structure_viewer_mcp`，工具目录在 `biomolecular_structure_viewer` 技能。聊天里必须带上 `sidecar://science/biomolecular_structure_viewer/ui.html`。
- **AutoDock Vina 1.2.5 与 smina**：`rdkit_general/scripts/compat.py` 在 `~/.local/bin` 查找或下载二进制。Vina 发布包是 linux x86_64；macOS 需要自行提供同名可执行文件。`cheminformatics_physics` 的对接脚本消费已经质子化的受体和配体。
- **Open Babel**：配体 pH 质子化通过 wheel 包 `openbabel-wheel`，不要求系统里另装 `obabel`。`format_converter_openbabel.py` 在缺少 Open Babel 时回退到 RDKit。
- **OpenMM、PDBFixer、OpenFF Sage（`openff-2.2.0`）**：受体质子化和小分子 MD。AM1-BCC 电荷需要 AmberTools 的 `sqm`（例如 `conda install -c conda-forge ambertools`）。未安装时 `--charge-method auto` 会警告并退回 RDKit MMFF94。
- **pdflatex + TikZ**：`render_mechanism.py` 优先用 `pdflatex` 画弯箭头机理图；找不到时改走 RDKit 回退渲染。
- **AlphaFold 3 容器**：本地 `--engine af3` 需要 Docker 或 Podman、镜像名 `alphafold3`，以及权重 `af3.bin.zst`（或 `af3.bin`）。查找顺序是 `--model-dir`、`AF3_MODEL_DIR`、`~/.cache/alphafold3`。有 NVIDIA GPU 时加 `--gpus all`（Podman 用 `--device=nvidia.com/gpu=all`）。CPU 路径设置 `XLA_PYTHON_CLIENT_PREALLOCATE=false`。非商业权重许可必须由用户确认。
- **Boltz-2 本地二进制**：PATH 上的 `boltz`，或 `uvx --python 3.11 --from boltz boltz predict`。Apple Silicon 设置 `PYTORCH_ENABLE_MPS_FALLBACK=1` 并用 `--accelerator mps`。
- **RDKit 技能根**：`RDKIT_SKILLS_ROOT` 可覆盖 `rdkit_general/scripts/skill_paths.py` 的技能定位。

---

## 4. 环境变量

凭据只放在 `~/.env` 或项目根 `.env`。技能 `credentials` 要求用 `grep -sq '^NAME=' ~/.env` 检查是否存在，禁止 `cat ~/.env`、不带 `-q` 的 `grep`、`echo $VAR` 或 `printenv`。脚本自己用 `python-dotenv` 加载。

```bash
# AlphaGenome（atlas、单变异、AVI 必需）
ALPHAGENOME_API_KEY="your_alphagenome_api_key_here"
# 可选。缺省时部分脚本回退到
# https://storage.googleapis.com/alphagenome/reference/gencode/ 上的 GENCODE GTF
ALPHAGENOME_GTF_PATH="/path/to/gencode.v46.annotation.gtf"

# NCBI E-utilities：无密钥 3 req/s，有密钥 10 req/s
# 用于 ClinVar、dbSNP、PubMed、NCBI sequence fetch
NCBI_API_KEY="your_ncbi_api_key"
NCBI_EMAIL="your_email@domain.com"
NCBI_TOOL="antigravity-science"
USER_EMAIL="your_email@domain.com"

# OpenAlex。content.openalex.org 需要密钥，否则走替代全文路径
OPENALEX_API_KEY="your_openalex_api_key"

# openFDA，可选，提高配额
FDA_API_KEY="your_openfda_api_key"

# 商业化合物报价。没有密钥时 pricer 只能读本地 --catalog-csv
MOLPORT_API_KEY="your_molport_api_key"
CHEMSPACE_API_KEY="your_chemspace_api_key"

# 共折叠引擎
AF3_MODEL_DIR="/path/to/alphafold3/weights"
BOLTZ_API_KEY="sk_bc_live_or_test"
BOLTZ_BASE_URL="https://api.boltz.bio"
OPENPROTEIN_USERNAME="your_openprotein_username"
OPENPROTEIN_API_KEY="your_openprotein_api_key"

# 可选
RDKIT_SKILLS_ROOT="/path/to/rdkit_general"
PYOPENGL_PLATFORM="osmesa"
```

`BOLTZ_API_KEY` 以 `sk_bc_*_test_*` 开头时，Boltz API 返回零成本占位结构；`sk_bc_*_live_*` 才跑 GPU 推理并返回 `binding_confidence` 和 `optimization_score`。

---

## 5. 许可记录

使用对应技能前，若工作区根目录还没有记录文件，技能会要求先通知用户条款再写入：

- `.licenses/predictingthepast_LICENSE.txt`
- `.licenses/pymol_LICENSE.txt`
- `.licenses/protein_sequence_similarity_search_LICENSE.txt`
- `.licenses/rdkit_general_LICENSE.txt`
- `.licenses/cheminformatics_core_LICENSE.txt`
- `.licenses/cheminformatics_doc2mol_LICENSE.txt`
- `.licenses/cheminformatics_ml_LICENSE.txt`
- `.licenses/cheminformatics_physics_LICENSE.txt`
- `.licenses/cheminformatics_reactions_LICENSE.txt`
- `.licenses/cheminformatics_cofolding_LICENSE.txt`

AlphaFold 3 权重另有 DeepMind 非商业条款，必须显式 `--accept-af3-terms`。各数据库技能还有自己的 `.licenses/<skill>_LICENSE.txt`。条款索引见 `science/SKILL_LICENSES.md`。
