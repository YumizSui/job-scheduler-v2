# Job Runner v2 - Quick Start Guide

## 5分でわかる使い方

### 1. CSVファイルを用意

```csv
param1,param2,JOBSCHEDULER_PRIORITY,JOBSCHEDULER_DEPENDS_ON
alpha,100,5,
beta,200,3,
gamma,300,8,alpha beta
```

- `JOBSCHEDULER_DEPENDS_ON`: 依存ジョブID（スペース区切り）
  - gammaはalphaとbetaが完了してから実行されます
- `JOBSCHEDULER_ESTIMATE_TIME`（任意列）: 推定実行時間（時間単位）。**デフォルトでは無視される**。`--smart-scheduling true` または `--longest-first` を付けたときだけ参照される（後述のパターン3を参照）

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
# 途中再開可能なジョブなら --max-runtime は付けない方が安全（中断されても次の worker が拾う）
# h_rt 内で確実に終わらせたい再開不可ジョブの場合のみ --max-runtime を指定する
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
db_util export input.db --csv-path done.csv --status done

# 失敗したジョブのみ
db_util export input.db --csv-path error.csv --status error
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
JOBSCHEDULER_JOB_ID,task,JOBSCHEDULER_PRIORITY,JOBSCHEDULER_DEPENDS_ON
preprocess,data_prep,10,
model_train,train_model,5,preprocess
model_eval,evaluate,3,model_train
report,generate_report,1,model_eval
```

→ preprocess → model_train → model_eval → report の順に実行される

### パターン3: 時間制約のあるジョブ

`--max-runtime` も `--smart-scheduling` も **デフォルトでは効かない**。次のいずれかに当てはまる場合だけ明示的に有効化する:

- 途中で打ち切られると最初からやり直しになるジョブ（FEPの一部、長時間 MD など）で h_rt 内に収めたい
- ジョブの実行時間が大きくばらつき、残り時間に詰める LPT/smart-scheduling が効率に効く

途中再開可能なジョブで安易に有効化するのは非推奨: `JOBSCHEDULER_ESTIMATE_TIME` の推定が外れたり `--max-runtime` を短くしたりすると **全ジョブが除外され、何も走らないまま課金だけされる** 事故が起きやすい。

```bash
# 24時間以内に終わらせる、最後の5分は余裕を持つ
# （--max-runtime のみ → 全体ループ&各ジョブの時間上限のみ。ESTIMATE_TIME は見ない）
job_scheduler jobs.db "bash run.sh" \
    --max-runtime 86400 \
    --margin-time 300

# ESTIMATE_TIME によるフィルタも有効化（残り時間に収まらないジョブを除外）
job_scheduler jobs.db "bash run.sh" \
    --max-runtime 86400 \
    --margin-time 300 \
    --smart-scheduling true
```

### パターン4: 失敗したジョブだけリトライ

```bash
# 1. 失敗ジョブをエクスポート
db_util export jobs.db --csv-path failed.csv --status error

# 2. 新しいDBにインポート
db_util import failed.csv --db-path retry.db

# 3. 再実行
job_scheduler retry.db "bash run.sh"
```

または：

```bash
# エラージョブのみpendingに戻す（heartbeatが生きているジョブは自動で保護）
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
db_util add new_jobs.csv --db-path jobs.db
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
```

`--smart-scheduling true` を明示的に付けている場合、`JOBSCHEDULER_ESTIMATE_TIME * 3600 / speed_factor` が `--max-runtime` の残りより大きいジョブはすべて除外され、何も走らない状態になる。デフォルトでは無効なのでこの挙動は起きないが、有効化していて止まっているなら外す（`--smart-scheduling false` または引数自体を外す）。

### Q: 途中でジョブが止まった

```bash
# progress_viewer / db_util stats / db_util recover のいずれかで双方向自動リカバリ
# heartbeatが2分以上途絶えた running → pending に戻し、
# heartbeatが生きているのに status がずれたジョブ → running に復帰
progress_viewer jobs.db
db_util stats jobs.db
db_util recover jobs.db

# job_scheduler 起動時にも stuck recovery が走ります
job_scheduler jobs.db "bash run.sh"

# 手動で全runningジョブをpendingに戻す場合
db_util reset jobs.db
```

### Q: `reset` でrunning中のジョブまで巻き戻してしまった

```bash
# heartbeatが生きているジョブは自動で running に復帰します。
# reset コマンドは実行直後にも自動 reconcile しますが、
# 手動で走らせたい場合は以下:
db_util recover jobs.db --direction mismatch
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

### Q: 実行中のジョブを止めたい

```bash
# 実行中ジョブのIDを確認
progress_viewer jobs.db

# 強制終了（schedulerがheartbeat時に検知し、プロセスグループ全体をSIGTERM→5秒後SIGKILL、最大30秒でerrorになる）
db_util kill jobs.db --jobs job_00000042
```

### Q: 並列実行してもあまり速くならない

- `--parallel` は1ノード内の並列数。CPU/GPU リソースに応じて指定（48 並列まで動作確認済み）
- ジョブ取得は BEGIN IMMEDIATE で直列化されるが claim 自体は数 ms オーダーなので並列度のボトルネックにはならない
- ジョブが軽すぎる（<1秒）とオーバーヘッドが目立つ
- ノード資源を超えたい場合は qsub アレイジョブで複数 worker を投入

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
