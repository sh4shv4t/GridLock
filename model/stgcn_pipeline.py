# %% [markdown]
# # Gridlock — STGCN ensemble (spatio-temporal graph conv)
#
# A second, more expressive model for the ensemble. The LightGBM model injects
# spatial structure as lag features; this one models it directly: H3 cells are
# **graph nodes**, ring-1 adjacency are **edges**, and each node carries a
# **time series** of violation counts. A 2-layer graph convolution aggregates
# across neighbours (capturing how congestion/parking pressure propagates between
# adjacent cells), then a GRU models the temporal dynamics to predict the next
# window's rate.
#
# Implemented in **plain PyTorch** (normalized-adjacency matmul — no
# torch_geometric), so it runs anywhere torch is available (Colab has it
# preinstalled). Best used as a second opinion alongside LightGBM, not a
# replacement: it captures spillover the tree model can't, but needs more data
# per node to shine.

# %%
# !pip install -q torch pandas numpy h3 scikit-learn
import os, json
import numpy as np
import pandas as pd

# %%
# ── CONFIG ──
def _find_csv():
    for p in [os.environ.get("GRIDLOCK_CSV", ""),
              "../jan to may police violation_anonymized791b166.csv",
              "/content/jan to may police violation_anonymized791b166.csv"]:
        if p and os.path.exists(p):
            return p
    raise FileNotFoundError("Set GRIDLOCK_CSV or upload the violation CSV.")

CSV_PATH   = _find_csv()
H3_RES     = 9
MIN_VIOL   = 10          # nodes = cells with >= this many violations
SEQ_LEN    = 7           # days of history per training window
HIDDEN     = 32
EPOCHS     = 15
DEVICE     = "cpu"

# %% [markdown]
# ## 1. Build the graph (nodes + ring-1 adjacency) and the node×time count matrix

# %%
import h3

def load_nodes_and_series(csv_path):
    df = pd.read_csv(csv_path, usecols=["latitude", "longitude", "created_datetime"], low_memory=False).dropna()
    df["h3"] = [h3.latlng_to_cell(la, lo, H3_RES) for la, lo in zip(df.latitude, df.longitude)]
    df["date"] = pd.to_datetime(df["created_datetime"], utc=True, errors="coerce").dt.tz_convert("Asia/Kolkata").dt.date
    df = df.dropna(subset=["date"])
    counts = df.groupby("h3").size()
    nodes = sorted(counts[counts >= MIN_VIOL].index)
    idx = {c: i for i, c in enumerate(nodes)}
    dates = sorted(df["date"].unique())
    didx = {d: i for i, d in enumerate(dates)}
    # node × day count matrix
    M = np.zeros((len(nodes), len(dates)), dtype=np.float32)
    sub = df[df["h3"].isin(idx)]
    for h3id, d in zip(sub["h3"], sub["date"]):
        M[idx[h3id], didx[d]] += 1
    # ring-1 adjacency among nodes
    A = np.eye(len(nodes), dtype=np.float32)
    for c in nodes:
        for nb in h3.grid_ring(c, 1):
            if nb in idx:
                A[idx[c], idx[nb]] = 1.0
    return nodes, M, A

nodes, M, A = load_nodes_and_series(CSV_PATH)
print(f"nodes={len(nodes)} | days={M.shape[1]} | edges={int(A.sum() - len(nodes))}")

# %%
# normalized adjacency  Â = D^-1/2 (A) D^-1/2  (A already has self-loops)
deg = A.sum(1)
Dinv = np.diag(1.0 / np.sqrt(np.clip(deg, 1e-6, None)))
A_hat = (Dinv @ A @ Dinv).astype(np.float32)

# %% [markdown]
# ## 2. STGCN model — 2× graph conv (spatial) → GRU (temporal) → next-day head

# %%
import torch, torch.nn as nn

class STGCN(nn.Module):
    def __init__(self, n_nodes, hidden=HIDDEN):
        super().__init__()
        self.gc1 = nn.Linear(1, hidden)
        self.gc2 = nn.Linear(hidden, hidden)
        self.gru = nn.GRU(hidden, hidden, batch_first=True)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x_seq, A_hat):
        # x_seq: (T, N, 1) ; A_hat: (N, N)
        T, N, _ = x_seq.shape
        h = []
        for t in range(T):
            z = torch.relu(A_hat @ self.gc1(x_seq[t]))   # spatial conv 1
            z = torch.relu(A_hat @ self.gc2(z))          # spatial conv 2
            h.append(z)
        h = torch.stack(h, 0).permute(1, 0, 2)           # (N, T, hidden)
        out, _ = self.gru(h)                             # temporal
        return self.head(out[:, -1, :]).squeeze(-1)      # (N,) next-day prediction

# %% [markdown]
# ## 3. Train (temporal split — predict the held-out tail) and evaluate

# %%
def make_windows(M, seq_len):
    X, Y = [], []
    for t in range(seq_len, M.shape[1]):
        X.append(M[:, t - seq_len:t].T[:, :, None])      # (seq_len, N, 1)
        Y.append(M[:, t])                                # (N,)
    return np.array(X, dtype=np.float32), np.array(Y, dtype=np.float32)

def train_stgcn(M, A_hat, seq_len=SEQ_LEN, epochs=EPOCHS):
    X, Y = make_windows(M, seq_len)
    n = len(X); split = int(n * 0.8)
    Xtr, Ytr, Xva, Yva = X[:split], Y[:split], X[split:], Y[split:]
    Ah = torch.tensor(A_hat, device=DEVICE)
    model = STGCN(M.shape[0]).to(DEVICE)
    opt = torch.optim.Adam(model.parameters(), lr=0.01)
    lossf = nn.L1Loss()
    for ep in range(epochs):
        model.train(); tot = 0.0
        for i in range(len(Xtr)):
            opt.zero_grad()
            pred = model(torch.tensor(Xtr[i], device=DEVICE), Ah)
            loss = lossf(pred, torch.tensor(Ytr[i], device=DEVICE))
            loss.backward(); opt.step(); tot += loss.item()
        if ep % 3 == 0 or ep == epochs - 1:
            model.eval()
            with torch.no_grad():
                vl = np.mean([lossf(model(torch.tensor(Xva[i], device=DEVICE), Ah),
                                    torch.tensor(Yva[i], device=DEVICE)).item()
                             for i in range(len(Xva))]) if len(Xva) else float("nan")
            print(f"epoch {ep:2d} | train MAE {tot/max(len(Xtr),1):.3f} | val MAE {vl:.3f}")
    return model

model = train_stgcn(M, A_hat)

# %% [markdown]
# ## 4. Predict next-day rate per node → export for the ensemble
# Blend with the LightGBM latent rate (e.g. average the normalized predictions)
# to form the ensemble priority score.

# %%
def export_predictions(model, M, A_hat, nodes, out="outputs/stgcn_pred.json"):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    Ah = torch.tensor(A_hat, device=DEVICE)
    x = torch.tensor(M[:, -SEQ_LEN:].T[:, :, None], device=DEVICE)
    model.eval()
    with torch.no_grad():
        pred = model(x, Ah).cpu().numpy()
    rows = [{"hotspot_id": c, "stgcn_next_day": float(max(p, 0.0))}
            for c, p in zip(nodes, pred)]
    json.dump(rows, open(out, "w"), indent=2)
    print(f"Wrote {len(rows)} STGCN predictions -> {out}")
    return rows

_ = export_predictions(model, M, A_hat, nodes)

# %% [markdown]
# ## Notes
# - Plain-PyTorch GCN (normalized-adjacency matmul) — no torch_geometric, so it
#   runs in Colab as-is.
# - This is an **ensemble member**, not a replacement for LightGBM. Blend the
#   normalized STGCN prediction with the LightGBM latent rate for the final score.
# - With more temporal resolution (3-hour windows, event flags) it captures
#   spillover dynamics the tabular model can't.
