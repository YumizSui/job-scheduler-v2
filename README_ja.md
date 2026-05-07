# Job Scheduler v2

SQLiteベースの並列ジョブスケジューラ（TSUBAME等のHPC環境向け）

[English README](README.md)

## 特徴

✅ **安全な並列アクセス**: SQLiteのアトミックトランザクションでマルチノード環境でも安全
✅ **ジョブ依存関係**: DAG形式の依存関係管理（ジョブAとBが完了後にジョブCを実行）
✅ **優先度スケジューリング**: 重要なジョブを優先実行
✅ **賢いスケジューリング**: 残り時間を考慮した実行判断
✅ **柔軟な引数渡し**: 位置引数・名前付き引数の両方に対応
✅ **リアルタイム出力**: stdout/stderrをリアルタイムでストリーム
✅ **CSV連携**: 簡単なデータ管理（インポート/エクスポート）
✅ **中断からの復旧**: 予期せぬ中断後も自動で復旧
✅ **進捗可視化**: 専用ビューアでリアルタイム監視（依存状態も表示）
✅ **DB閲覧CLI**: `show`/`list`/`stats --by` — CSVに吐かずジョブを直接検索・閲覧
✅ **インタラクティブTUI**: `job_tui` — ↑↓で選択・`/`フィルタ・`Enter`詳細表示

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

```bash
git clone <repository>
cd job-scheduler-v2
chmod +x script/job_scheduler script/db_util script/progress_viewer script/job_tui

# パスを通す
export PATH="$(pwd)/script:$PATH"

# 永続化する場合は ~/.bashrc に追加
echo 'export PATH="/path/to/job-scheduler-v2/script:$PATH"' >> ~/.bashrc
```

`job_scheduler` は Python 標準ライブラリのみで動作します。`db_util show`/`list`/`stats --by` と `job_tui` の実行には `.venv` が必要です：

```bash
uv sync   # .venv を構築（rich + textual をインストール）
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

### データベース閲覧

```bash
# 単一ジョブの全カラム（引数・ステータス・エラー内容・タイムスタンプ）を表示
db_util show job_00000003 --db-path jobs.db

# 整形テーブルでジョブ一覧（デフォルトはユーザ列 + 主要カラム）
db_util list --db-path jobs.db

# フィルタ：errorでCUDAメモリ不足のもの
db_util list --db-path jobs.db --status error --grep-error "CUDA out of memory"

# 特定ワーカー・優先度範囲・ソート指定
db_util list --db-path jobs.db --worker "hostname:12345" --priority-min 5 \
  --sort JOBSCHEDULER_ELAPSED_TIME

# 表示カラムを指定
db_util list --db-path jobs.db --columns JOBSCHEDULER_JOB_ID,JOBSCHEDULER_STATUS,paramA,paramB

# 統計（worker別・priority別の内訳）
db_util stats jobs.db --by worker
db_util stats jobs.db --by priority
```

### データベース管理

```bash
# CSV → SQLite（自動的にファイル名から.dbに変換）
db_util import jobs.csv

# 出力先を明示的に指定
db_util import input.csv --db-path jobs.db

# 既存DBにジョブ追加（スキーマ整合性チェック付き）
db_util add new_jobs.csv --db-path jobs.db

# SQLite → CSV（機械処理・他ツール連携用）
db_util export jobs.db
db_util export jobs.db --csv-path done.csv --status done

# 統計表示（heartbeatとstatusの齟齬を双方向に自動リカバリ）
db_util stats jobs.db

# heartbeatとstatusの齟齬を手動で解消
db_util recover jobs.db                         # 両方向（デフォルト）
db_util recover jobs.db --direction stuck       # running→pending のみ
db_util recover jobs.db --direction mismatch    # pending/error→running のみ

# すべてのジョブをpendingにリセット（実行中ジョブはheartbeatで保護）
db_util reset jobs.db

# エラージョブのみpendingにリセット
db_util reset jobs.db --status error

# 特定のジョブIDのみpendingにリセット
db_util reset jobs.db --jobs job_00000000,job_00000001

# 特定IDかつエラーのみリセット（両条件のAND）
db_util reset jobs.db --jobs job_00000000,job_00000001 --status error

# ジョブをdone/errorステータスに設定
db_util reset jobs.db --set-status done --jobs job_00000005
db_util reset jobs.db --set-status error --status running

# 実行中のジョブを強制終了
db_util kill jobs.db --jobs job_00000042
db_util kill jobs.db --jobs job_00000042,job_00000043
```

### インタラクティブTUI

```bash
# DB を TUI でブラウズ（閲覧専用、5秒ごとに自動リロード）
job_tui jobs.db

# reset / kill 操作を有効化（確認モーダルあり）
job_tui jobs.db --enable-actions

# 自動リフレッシュを無効化（r キーで手動更新）
job_tui jobs.db --no-auto-refresh

# リフレッシュ間隔を調整（デフォルト5秒、40並列ワーカー規模でも安全）
job_tui jobs.db --refresh-interval 10
```

**キー操作**:
- `↑↓` でスクロール、`Enter` で詳細パネル表示
- `/` でフィルタ入力（`status=error` `worker=host1` や自由テキストで全カラム部分一致検索）
- `s` でソート列切替、`p` で一時停止、`r` で手動リフレッシュ
- `q` / `Esc` で終了（確認ダイアログあり）
- `--enable-actions` 時: `Ctrl+R` でreset→pending、`Ctrl+K` でkill（確認あり）

### 進捗監視

```bash
# 1回だけ表示（heartbeatとstatusの齟齬を双方向に自動リカバリ）
progress_viewer jobs.db

# リアルタイム監視（2秒ごとに更新）
progress_viewer jobs.db --watch

# 更新間隔を変更
progress_viewer jobs.db --watch --interval 5

# 自動リカバリを無効化（両方向とも止まる）
progress_viewer jobs.db --no-recover

# stale/fresh判定の閾値を変更（デフォルト120秒）
progress_viewer jobs.db --stale-threshold 300
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
  --longest-first       同一優先度内で推定時間が長いジョブを優先（LPT戦略、デフォルト: false）
  --named-args          名前付き引数モード（--key value形式）
  --parallel N          並列実行数（デフォルト: 1）
  --dep-wait-interval SEC  依存待ち時の待機間隔（秒）（デフォルト: 30）
  --heartbeat-interval SEC ハートビート更新間隔（秒）（デフォルト: 30）
  --stale-threshold SEC    stuck判定の閾値（秒）（デフォルト: 120）
  --jobs JOB_IDS        カンマ区切りのジョブIDを最優先で実行。完了後は通常スケジューリングに移行
  --jobs-only           --jobsで指定したジョブのみ実行して終了（--jobsと併用）
  --log-stderr          ログ出力をstderrに切り替え（デフォルト: stdout）
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
- `JOBSCHEDULER_HEARTBEAT` - ワーカーの最終生存確認時刻
- `JOBSCHEDULER_WORKER_ID` - ワーカー識別子（hostname:PID）
- `JOBSCHEDULER_KILL_REQUESTED` - 強制終了リクエストのタイムスタンプ（`db_util kill`でセット、終了後NULLにリセット）

## 動作の仕組み

### ジョブ実行フロー

1. **ジョブ選択**: `pending`状態かつ依存関係が満たされたジョブを取得
   - 依存ジョブが全て`done`になっているジョブのみ選択
   - `JOBSCHEDULER_PRIORITY`の降順でソート
   - `smart-scheduling=true`の場合、残り時間内に収まるジョブのみ選択
   - `--longest-first`の場合、同一優先度内で`JOBSCHEDULER_ESTIMATE_TIME`の降順でソート（LPT戦略）

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

- **BEGIN IMMEDIATE による直列化**: ジョブ取得は SELECT+UPDATE を 1 トランザクションにまとめ、SQLite の書込みロックでワーカー間を直列化。重複取得や race の心配なく 48 並列クラスでも安定動作
- **busy_timeout=30秒**: 他ワーカーがロック中はブロック待機。タイムアウト時は指数バックオフで最大5回リトライ
- **アトミック更新**: すべてのステータス変更はトランザクション内で実行
- **ハートビート方式**: 実行中ジョブは30秒ごとに生存を通知
- **双方向 Auto Recovery**: heartbeat と DB status の齟齬を `progress_viewer`・`db_util stats`・`db_util reset`・`db_util recover` 実行時に自動解消
  - `running` のまま heartbeat が2分以上途絶えたジョブ → `pending` に戻してリスケ対象に
  - heartbeat が生きている（worker が touch し続けている）のに status が `running` 以外に外れたジョブ → `running` に復帰（reset の誤爆等を保護）

## トラブルシューティング

### Q: ジョブが`running`状態で止まっている

```bash
# progress_viewer / db_util stats / db_util recover のいずれかで双方向自動リカバリ
# （heartbeatが2分以上途絶えたジョブのみpendingに戻す。生きているジョブは保護）
progress_viewer jobs.db
db_util stats jobs.db
db_util recover jobs.db

# job_scheduler 起動時にも stuck recovery が走ります
job_scheduler jobs.db "bash run.sh"

# 手動で全ジョブをpendingにリセットする場合
db_util reset jobs.db
```

### Q: `reset` で誤ってrunning中のジョブをerror/pendingにしてしまった

```bash
# heartbeatが生きている（worker が touch し続けている）ジョブは
# reset 直後や db_util recover で自動的に running に復帰します。
db_util recover jobs.db --direction mismatch
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
# エラー内容を確認
db_util list --db-path jobs.db --status error --grep-error "."
db_util show job_00000003 --db-path jobs.db

# エラージョブのみリセットして再実行
db_util reset jobs.db --status error
# 特定IDのジョブのみリセット
db_util reset jobs.db --jobs job_00000000,job_00000001
job_scheduler jobs.db "bash run.sh"
```

### Q: 実行中のジョブを止めたい

```bash
# 実行中のジョブIDを確認
progress_viewer jobs.db

# 強制終了（schedulerがheartbeat時に検知し、プロセスグループ全体をSIGTERM→5秒後SIGKILL、最大30秒でerrorになる）
db_util kill jobs.db --jobs job_00000042

# 複数ジョブをまとめて強制終了
db_util kill jobs.db --jobs job_00000042,job_00000043
```

### Q: データベースロックエラー

- 通常は高競合時に発生し、自動的にリトライされます
- 持続する場合は、長時間実行中のトランザクションやロックをチェック

### Q: 並列実行しても速くならない

- `--parallel`は1ノード内の並列数です。CPU/GPU リソースに応じて指定（48 並列まで動作確認済み）
- ジョブが軽すぎる（<1秒）場合はオーバーヘッドの影響が大きい
- ノード資源を超える並列は意味なし。複数ノードに分散したい場合は qsub アレイジョブで複数 worker を投入

## ライセンス

親ディレクトリのLICENSEファイルを参照してください。

## 参考資料

- [QUICKSTART.md](QUICKSTART.md) - 5分でわかる使い方
- [SETUP.md](SETUP.md) - インストールとセットアップ
- [README.md](README.md) - English documentation
