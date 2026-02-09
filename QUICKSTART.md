# Job Runner v2 - Quick Start Guide

## 5分でわかる使い方

### 1. CSVファイルを用意

```csv
param1,param2,JOBSCHEDULER_PRIORITY,JOBSCHEDULER_ESTIMATE_TIME
alpha,100,5,0.5
beta,200,3,0.3
gamma,300,8,0.1
```

### 2. SQLiteにインポート

```bash
db_util import jobs.db input.csv
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
job_scheduler jobs.db run.sh
```

**シングルノード（4並列）**:
```bash
job_scheduler jobs.db run.sh --parallel 4
```

**複数ノード（qsub）**:

```bash
# qsub_worker.sh
#!/bin/bash
#$ -cwd
#$ -l cpu_4=1
#$ -l h_rt=24:00:00

source $HOME/.bashrc
/path/to/job_scheduler /path/to/jobs.db "bash run.sh" \
    --max-runtime 86000 \
    --margin-time 300
```

```bash
# 10ワーカーを投入
# アレイジョブで投入（推奨）
miqsub -t 1-10 qsub_worker.sh
```

### 5. 進捗確認

```bash
# 1回だけ確認
progress_viewer jobs.db

# リアルタイム監視
progress_viewer jobs.db --watch
```

### 6. 結果をCSVにエクスポート

```bash
# すべてのジョブ
db_util export jobs.db output.csv

# 完了したジョブのみ
db_util export jobs.db done.csv --status done

# 失敗したジョブのみ
db_util export jobs.db error.csv --status error
```

## よくある使い方

### パターン1: 大量の実験パラメータを試す

```bash
# 1. パラメータCSV生成
./tests/production/generate_jobs.py > experiments.csv

# 2. DB作成
db_util import experiments.db experiments.csv

# 3. 複数ノードで実行（アレイジョブで投入）
miqsub -t 1-20 worker.sh  # 20ワーカーで並列実行

# 4. 進捗監視
watch -n 5 'progress_viewer experiments.db'
```

### パターン2: 優先度付きジョブ

```csv
task,priority,estimate_time
urgent_task,10,1.0
normal_task1,5,0.5
normal_task2,5,0.5
low_priority,1,2.0
```

→ urgent_task が最優先で実行される

### パターン3: 時間制約のあるジョブ

```bash
# 24時間以内に終わらせる、最後の5分は余裕を持つ
job_scheduler jobs.db run.sh \
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
job_scheduler retry.db run.sh
```

## トラブルシューティング

### Q: ジョブが実行されない

```bash
# ステータス確認
db_util stats jobs.db

# すべてpendingなのに実行されない場合は、estimate_timeをチェック
# estimate_time が大きすぎると、残り時間内に収まらないので実行されない
```

### Q: 途中でジョブが止まった

```bash
# runningで止まっているジョブをpendingに戻す
db_util reset jobs.db
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
- [README_TEST.md](README_TEST.md) - テストガイド
- [SETUP.md](SETUP.md) - インストールとセットアップ
