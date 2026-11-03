# GPU & platform notes (hard-won operational rules)

These rules come from actually running the reference benchmark. Ignoring them wastes
GPU time and hours. Read before launching GPU work.

## 1. Use a ManageMachine GPU machine and run long steps in the background

Create the machine with `ManageMachine` (`action: "create"`, a machine of `type: "gpu"`,
`spec.gpu: "A10G"`) and run every step with `Bash`, passing its `machine_id`. A foreground
`Bash` call is capped at 60 minutes, so start prediction and fine-tuning with
`run_in_background: true` and poll with `BashOutput`. A running background job keeps the
machine awake. An idle machine hibernates and wakes with `/workspace` restored; deleting
the machine loses `/workspace`.

## 2. Use in-script time caps + checkpoints for long training

Long jobs must still protect themselves against a crash or an accidental delete:
- `train_scgpt.py` has `--time_cap_min`: it stops after the epoch that would exceed the
  cap, saves the best checkpoint, and writes `state.json`.
- It also supports `--resume`: rerun to continue from the last saved checkpoint. Prefer
  several short capped runs over one long run.
- **Save prediction arrays IMMEDIATELY** after the forward pass, before any metric code
  (`predict.py` does this). If downstream code fails, the expensive GPU output survives.

## 3. Batch size and CUDA memory

- Attention cost is O(seq²) and the Norman gene set is ~5045 genes, so memory scales
  sharply with batch size. **Batch 32 is safe (~8 GB); batch 64 OOMs** on a 24 GB A10G.
- Always export before the run:
  ```
  export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
  ```
- Default GPU for this workload: **A10G (24 GB)** is sufficient. Larger gene sets or
  bigger batches may need an A100.

## 4. S3 FUSE mounts do not support random-access writes

`/mnt/results` and `/mnt/shared-workspace` are S3-backed. Writing `.pt`, `.npy`, `.h5ad`,
`.db`, etc. directly there can corrupt or fail. Pattern used throughout the scripts:

```python
np.save("/workspace/preds/test_pred.npy", pred)          # local disk
shutil.copy("/workspace/preds/test_pred.npy", out_dir)   # then copy to /mnt/results
torch.save(sd, "/workspace/.../best_model.pt"); shutil.copy(..., save_dir)  # same for .pt
```

CSV / JSON / PNG / SVG / gzip write fine directly to `/mnt/results`. In **R**,
`file.copy()` to `/mnt/results` produces 0-byte files — use a shell `cp` instead.

## 5. Environment must be bootstrapped from scratch

scGPT and GEARS are **not** pre-installed; verify with direct imports if needed. Build
the environment once with `scripts/setup_env.sh` (conda, python=3.10). Pinned,
order-sensitive recipe:

- `torch==2.1.0 torchvision==0.16.0 torchtext==0.16.0` (cu121 index).
- `"numpy<2"`, `torch-geometric==2.4.0`, then `torch-scatter/torch-sparse/torch-cluster`
  from the `torch-2.1.0+cu121` wheel index.
- `cell-gears==0.0.2 --no-deps`, `scgpt==0.2.4 --no-deps` (install `--no-deps` to avoid
  pulling incompatible transitive pins), then the scanpy/anndata/etc. stack with
  `"pandas<2.2"`.
- On `/workspace`, `pip`/conda envs and downloaded weights **persist across idle
  hibernation**; keep the same GPU machine instead of deleting it to avoid re-installing.

## 6. Download robustness

- The `scGPT_human` Google-Drive **folder** download is flaky and sometimes drops
  `args.json`. Download the three files **individually by id** (`get_checkpoint.py --which base`).
- Verify sizes after download: `args.json` ~1.3 KB, `vocab.json` ~1.32 MB,
  base `best_model.pt` ~205 MB, norman-ft `best_model.pt` ~207 MB. A tiny `best_model.pt`
  usually means an unconfirmed Drive "are you sure" HTML page — retry with `gdown` (it
  handles the confirm token).

## 7. Misc gotchas
- `np.float` is removed in NumPy ≥ 1.24 — use `float` or `np.float32`/`np.float64`.
- Set `logging.getLogger("scgpt").setLevel(logging.ERROR)` to silence noisy per-batch logs.
- Run a `Read(file_path=...)` on every saved figure PNG and on the final PDF;
  regenerate anything blank, clipped, or low-quality before finishing.

## 8. GPU limits
`ManageMachine` refuses a GPU machine past the session's GPU cap; delete one before
creating another. The `hpc_run_tool` path can separately return **429** when the
concurrent GPU-job cap is hit. There is no automatic retry — tell the user and wait, or
reduce concurrency.
