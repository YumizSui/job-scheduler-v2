# Job Scheduler v2

SQLiteベースの並列ジョブスケジューラ（TSUBAME等のHPC環境向け）

[English README](README.md)

## 特徴

✅ **安全な並列アクセス**: SQLite（WALモード）でマルチノード環境でも安全
✅ **ジョブ依存関係**: DAG形式の依存関係管理（ジョブAとBが完了後にジョブCを実行）
✅ **優先度スケジューリング**: 重要なジョブを優先実行
✅ **賢いスケジューリング**: 残り時間を考慮した実行判断
✅ **柔軟な引数渡し**: 位置引数・名前付き引数の両方に対応
✅ **リアルタイム出力**: stdout/stderrをリアルタイムでストリーム
✅ **CSV連携**: 簡単なデータ管理（インポート/エクスポート）
✅ **中断からの復旧**: 予期せぬ中断後も自動で復旧
✅ **進捗可視化**: 専用ビューアでリアルタイム監視（依存状態も表示）

## クイックスタート

### 1. ジョブCSVを用意

```csv
param1,param2,JOBSCHEDULER_PRIORITY,JOBSCHEDULER_ESTIMATE_TIME,JOBSCHEDULER_DEPENDS_ON
preprocess,data1,5,0.5,
training,model1,3,2.0,preprocess
evaluation,results,1,0.3,training
```

### 2. SQLiteにインポート

```bash
# 自動的に experiments.db にインポート
db_util import experiments.csv
```

### 3. 実行

```bash
# 位置引数で実行（シェルスクリプト）
job_scheduler experiments.db "bash run.sh"

# 名前付き引数で実行（Pythonスクリプト）
job_scheduler experiments.db "python run.py" --named-args

# 並列実行（2ワーカー、依存関係を自動管理）
job_scheduler experiments.db "bash run.sh" --parallel 2
```

### 4. 進捗確認

```bash
# リアルタイム監視（依存状態も表示）
progress_viewer experiments.db --watch
```

## インストール

標準ライブラリのみで動作します（Python 3.6+）：

```bash
git clone <repository>
cd job-scheduler-v2
chmod +x script/job_scheduler script/db_util script/progress_viewer

# パスを通す
export PATH="$(pwd)/script:$PATH"

# 永続化する場合は ~/.bashrc に追加
echo 'export PATH="/path/to/job-scheduler-v2/script:$PATH"' >> ~/.bashrc
```

## 使い方

### 基本的な使用例

```bash
# シングルノードで実行
job_scheduler jobs.db "bash run.sh"

# 並列実行（1ノード内で4並列）
job_scheduler jobs.db "bash run.sh" --parallel 4

# 時間制約付き（24時間以内、最後5分はマージン）
job_scheduler jobs.db "bash run.sh" \
    --max-runtime 86400 \
    --margin-time 300
```

### TSUBAMEでの複数ノード実行

**ジョブスクリプト (qsub_worker.sh)**:
```bash
#!/bin/bash
#$ -cwd
#$ -l cpu_4=1
#$ -l h_rt=24:00:00
#$ -N my_job

source $HOME/.bashrc

job_scheduler /path/to/jobs.db "bash run.sh" \
    --max-runtime 86000 \
    --margin-time 300
```

**ジョブ投入**:
```bash
# アレイジョブで10ワーカーを投入
miqsub -t 1-10 qsub_worker.sh
```

### ジョブ依存関係

CSVの`JOBSCHEDULER_DEPENDS_ON`列にスペース区切りで依存ジョブIDを指定：

```csv
JOBSCHEDULER_JOB_ID,task,JOBSCHEDULER_DEPENDS_ON
jobA,preprocess,
jobB,load_data,
jobC,training,jobA jobB
jobD,evaluation,jobC
```

→ jobA と jobB が完了してから jobC が実行され、その後 jobD が実行されます。

依存ジョブが`error`の場合、その依存関係を持つジョブは永久にブロックされますが、スケジューラは自動的に停止します。

### データベース管理

```bash
# CSV → SQLite（自動的にファイル名から.dbに変換）
db_util import jobs.csv

# 従来通りの指定も可能
db_util import jobs.db input.csv

# 既存DBにジョブ追加（スキーマ整合性チェック付き）
db_util add new_jobs.csv  # 自動的に new_jobs.db に追加
db_util add jobs.db new_jobs.csv  # 明示的な指定も可能

# SQLite → CSV（自動的にファイル名から.csvに変換）
db_util export jobs.db

# フィルタ付きエクスポート
db_util export jobs.db done.csv --status done
db_util export jobs.db error.csv --status error

# 統計表示
db_util stats jobs.db

# すべてのジョブをpendingにリセット
db_util reset jobs.db

# エラージョブのみpendingにリセット
db_util reset jobs.db --status error
```

### 進捗監視

```bash
# 1回だけ表示
progress_viewer jobs.db

# リアルタイム監視（2秒ごとに更新）
progress_viewer jobs.db --watch

# 更新間隔を変更
progress_viewer jobs.db --watch --interval 5
```

**進捗表示の見方**:
```
Pending: 10 (50.0%)
  - Ready:     2  ← 今すぐ実行可能
  - Waiting:   7  ← 依存ジョブ待ち
  - Blocked:   1  ← エラーでブロック
```

## コマンドラインオプション

```
job_scheduler <db_file> <command> [options]

必須引数:
  db_file               SQLiteデータベースファイルのパス
  command               各ジョブで実行するコマンド

オプション:
  --max-runtime SEC     最大実行時間（秒）（デフォルト: 86400 = 24時間）
  --margin-time SEC     安全マージン時間（秒）（デフォルト: 0）
  --speed-factor FLOAT  時間推定の速度係数（デフォルト: 1.0）
  --smart-scheduling    賢いスケジューリングを有効化（デフォルト: true）
  --named-args          名前付き引数モード（--key value形式）
  --parallel N          並列実行数（デフォルト: 1）
  --dep-wait-interval SEC  依存待ち時の待機間隔（秒）（デフォルト: 30）
```

## 予約カラム名

すべての予約カラムは`JOBSCHEDULER_`で始まります：

- `JOBSCHEDULER_JOB_ID` - ジョブの一意識別子
- `JOBSCHEDULER_STATUS` - ジョブのステータス（pending/running/done/error）
- `JOBSCHEDULER_PRIORITY` - 優先度（大きいほど先に実行）
- `JOBSCHEDULER_ESTIMATE_TIME` - 推定実行時間（時間単位）
- `JOBSCHEDULER_ELAPSED_TIME` - 実際の実行時間（秒単位）
- `JOBSCHEDULER_DEPENDS_ON` - 依存ジョブID（スペース区切り）
- `JOBSCHEDULER_CREATED_AT` - 作成日時
- `JOBSCHEDULER_STARTED_AT` - 開始日時
- `JOBSCHEDULER_FINISHED_AT` - 終了日時
- `JOBSCHEDULER_ERROR_MESSAGE` - エラーメッセージ

## 動作の仕組み

### ジョブ実行フロー

1. **ジョブ選択**: `pending`状態かつ依存関係が満たされたジョブを取得
   - 依存ジョブが全て`done`になっているジョブのみ選択
   - `JOBSCHEDULER_PRIORITY`の降順でソート
   - `smart-scheduling=true`の場合、残り時間内に収まるジョブのみ選択

2. **ステータス更新**: `running`に変更、`JOBSCHEDULER_STARTED_AT`を記録

3. **コマンド実行**:
   - **位置引数モード**: `command param1 param2 param3 ...`
   - **名前付き引数モード**: `command --param1 value1 --param2 value2 ...`

4. **完了処理**: `done`または`error`に更新、`elapsed_time`を記録

### 依存関係の管理

- スケジューラは依存ジョブが全て`done`になるまで待機
- 依存ジョブが`error`の場合、そのジョブは永久にブロックされます
- ブロックされたジョブのみが残っている場合、スケジューラは自動的に停止
- `--dep-wait-interval`で待機間隔を調整可能（デフォルト30秒）

### マルチノード安全性

- **WALモード**: 複数リーダー + 1ライター同時アクセス
- **BEGIN IMMEDIATE**: 早期にロックを取得して競合を検出
- **busy_timeout=30秒**: ロック競合時は自動リトライ
- **アトミック更新**: すべてのステータス変更はトランザクション内で実行
- **Stuck Job Recovery**: 起動時に`running`状態で止まっているジョブを自動的に`pending`に復旧

## トラブルシューティング

### Q: ジョブが`running`状態で止まっている

```bash
# スケジューラを再起動すると自動的にpendingに戻ります
job_scheduler jobs.db "bash run.sh"

# または手動でリセット
db_util reset jobs.db
```

### Q: ジョブが実行されない

```bash
# ステータス確認（依存状態も表示）
progress_viewer jobs.db

# estimate_timeが大きすぎて残り時間内に収まらない場合
# → smart-schedulingを無効化
job_scheduler jobs.db "bash run.sh" --smart-scheduling false
```

### Q: 依存ジョブがエラーでブロックされている

```bash
# ブロックされたジョブを確認
progress_viewer jobs.db

# エラージョブのみリセットして再実行
db_util reset jobs.db --status error
job_scheduler jobs.db "bash run.sh"
```

### Q: データベースロックエラー

- 通常は高競合時に発生し、自動的にリトライされます
- 持続する場合は、長時間実行中のトランザクションやロックをチェック

### Q: 並列実行しても速くならない

- `--parallel`は1ノード内の並列数です。複数ノード投入の方が効率的
- ジョブが軽すぎる（<1秒）場合はオーバーヘッドの影響が大きい
- 推奨：1ノード内は2-4並列程度、それ以上は複数ノード投入を推奨

## ライセンス

親ディレクトリのLICENSEファイルを参照してください。

## 参考資料

- [QUICKSTART.md](QUICKSTART.md) - 5分でわかる使い方
- [SETUP.md](SETUP.md) - インストールとセットアップ
- [README.md](README.md) - English documentation
