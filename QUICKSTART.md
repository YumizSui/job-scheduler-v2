# Job Runner v2 - Quick Start Guide

## 5分でわかる使い方

### 1. CSVファイルを用意

```csv
param1,param2,JOBSCHEDULER_PRIORITY,JOBSCHEDULER_ESTIMATE_TIME,JOBSCHEDULER_DEPENDS_ON
alpha,100,5,0.5,
beta,200,3,0.3,
gamma,300,8,0.1,alpha beta
```

- `JOBSCHEDULER_DEPENDS_ON`: 依存ジョブID（スペース区切り）
  - gammaはalphaとbetaが完了してから実行されます

### 2. SQLiteにインポート

```bash
# 自動的に input.db にインポート
db_util import input.csv
```

### 3. 実行スクリプトを用意

**位置引数版 (run.sh)**:
```bash
#!/bin/bash
param1=$1
param2=$2
echo "Processing: $param1, $param2"
# ... your code here ...
```

**名前付き引数版 (run.py)**:
```python
import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--param1')
parser.add_argument('--param2')
args = parser.parse_args()
print(f"Processing: {args.param1}, {args.param2}")
# ... your code here ...
```

### 4. ジョブを実行

**シングルノード（1プロセス）**:
```bash
job_scheduler input.db "bash run.sh"
```

**シングルノード（4並列、依存関係も自動管理）**:
```bash
job_scheduler input.db "bash run.sh" --parallel 4
```

**複数ノード（qsub）**:

```bash
# qsub_worker.sh
#!/bin/bash
#$ -cwd
#$ -l cpu_4=1
#$ -l h_rt=24:00:00

source $HOME/.bashrc
job_scheduler /path/to/input.db "bash run.sh" \
    --max-runtime 86000 \
    --margin-time 300
```

```bash
# 10ワーカーを投入（アレイジョブで投入推奨）
miqsub -t 1-10 qsub_worker.sh
```

### 5. 進捗確認

```bash
# 1回だけ確認
progress_viewer input.db

# リアルタイム監視（依存状態も表示）
progress_viewer input.db --watch
```

**表示例**:
```
Statistics:
  Total jobs:    3
  Pending:       1 (33.3%)
    - Ready:     0  ← 今すぐ実行可能
    - Waiting:   1  ← 依存ジョブ待ち
    - Blocked:   0  ← エラーでブロック
  Running:       0
  Completed:     2 (66.7%)
```

### 6. 結果をCSVにエクスポート

```bash
# すべてのジョブ（自動的に input.csv にエクスポート）
db_util export input.db

# 完了したジョブのみ
db_util export input.db done.csv --status done

# 失敗したジョブのみ
db_util export input.db error.csv --status error
```

## よくある使い方

### パターン1: 大量の実験パラメータを試す

```bash
# 1. パラメータCSV生成
./tests/production/generate_jobs.py > experiments.csv

# 2. DB作成（自動的に experiments.db が作成される）
db_util import experiments.csv

# 3. 複数ノードで実行（アレイジョブで投入）
miqsub -t 1-20 worker.sh  # 20ワーカーで並列実行

# 4. 進捗監視
watch -n 5 'progress_viewer experiments.db'
```

### パターン2: 優先度付き+依存関係のあるジョブ

```csv
JOBSCHEDULER_JOB_ID,task,JOBSCHEDULER_PRIORITY,JOBSCHEDULER_ESTIMATE_TIME,JOBSCHEDULER_DEPENDS_ON
preprocess,data_prep,10,1.0,
model_train,train_model,5,5.0,preprocess
model_eval,evaluate,3,0.5,model_train
report,generate_report,1,0.3,model_eval
```

→ preprocess → model_train → model_eval → report の順に実行される

### パターン3: 時間制約のあるジョブ

```bash
# 24時間以内に終わらせる、最後の5分は余裕を持つ
job_scheduler jobs.db "bash run.sh" \
    --max-runtime 86400 \
    --margin-time 300
```

### パターン4: 失敗したジョブだけリトライ

```bash
# 1. 失敗ジョブをエクスポート
db_util export jobs.db failed.csv --status error

# 2. 新しいDBにインポート
db_util import retry.db failed.csv

# 3. 再実行
job_scheduler retry.db "bash run.sh"
```

または：

```bash
# エラージョブのみpendingに戻す
db_util reset jobs.db --status error
job_scheduler jobs.db "bash run.sh"
```

### パターン5: 既存のDBにジョブを追加

```bash
# 新しいジョブをCSVで作成
cat > new_jobs.csv <<EOF
param1,param2
new_exp1,100
new_exp2,200
EOF

# 既存DBに追加（スキーマ整合性チェック付き）
db_util add jobs.db new_jobs.csv
```

## トラブルシューティング

### Q: ジョブが実行されない

```bash
# ステータス確認（依存状態も含む）
progress_viewer jobs.db

# すべてpendingなのに実行されない場合は、依存関係をチェック
# - Waiting: 依存ジョブが running/pending
# - Blocked: 依存ジョブが error
# - Ready: すぐに実行可能

# estimate_time が大きすぎると、残り時間内に収まらないので実行されない
```

### Q: 途中でジョブが止まった

```bash
# runningで止まっているジョブをpendingに戻す
db_util reset jobs.db

# エラージョブのみリセット
db_util reset jobs.db --status error
```

### Q: 依存ジョブがエラーでブロックされている

```bash
# ブロック状態を確認
progress_viewer jobs.db
# → "Blocked: N" が表示される

# エラーになったジョブだけリセットして再実行
db_util reset jobs.db --status error
job_scheduler jobs.db "bash run.sh"
```

### Q: 並列実行してもあまり速くならない

- SQLiteのロック競合が発生している可能性
- `--parallel` は1ノード内の並列数なので、複数ノード投入の方が効率的
- または、ジョブが軽すぎてオーバーヘッドが大きい

### Q: 進捗ビューアが動かない

```bash
# データベースファイルのパスが正しいか確認
ls -la jobs.db

# 読み取り権限があるか確認
python3 -c "import sqlite3; conn = sqlite3.connect('jobs.db'); print('OK')"
```


## 次のステップ

- [README.md](README.md) / [README_ja.md](README_ja.md) - 詳細なドキュメント
- [SETUP.md](SETUP.md) - インストールとセットアップ
