# Job Scheduler v2

SQLiteベースの並列ジョブスケジューラ（TSUBAME等のHPC環境向け）

[English README](README.md)

## 特徴

✅ **安全な並列アクセス**: SQLite（WALモード）でマルチノード環境でも安全
✅ **優先度スケジューリング**: 重要なジョブを優先実行
✅ **賢いスケジューリング**: 残り時間を考慮した実行判断
✅ **柔軟な引数渡し**: 位置引数・名前付き引数の両方に対応
✅ **リアルタイム出力**: stdout/stderrをリアルタイムでストリーム
✅ **CSV連携**: 簡単なデータ管理（インポート/エクスポート）
✅ **中断からの復旧**: 予期せぬ中断後も自動で復旧
✅ **進捗可視化**: 専用ビューアでリアルタイム監視

## クイックスタート

### 1. ジョブCSVを用意

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

### 3. 実行

```bash
# 位置引数で実行（シェルスクリプト）
job_scheduler jobs.db run.sh

# 名前付き引数で実行（Pythonスクリプト）
job_scheduler jobs.db "python run.py" --named-args
```

### 4. 進捗確認

```bash
# リアルタイム監視
progress_viewer jobs.db --watch
```

## インストール

標準ライブラリのみで動作します（Python 3.6+）：

```bash
git clone <repository>
cd job-runner-v2
chmod +x job_scheduler db_util.py progress_viewer.py
```

## 使い方

### 基本的な使用例

```bash
# シングルノードで実行
job_scheduler jobs.db run.sh

# 並列実行（1ノード内で4並列）
job_scheduler jobs.db run.sh --parallel 4

# 時間制約付き（24時間以内、最後5分はマージン）
job_scheduler jobs.db run.sh \
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

/path/to/job_scheduler /path/to/jobs.db "bash run.sh" \
    --max-runtime 86000 \
    --margin-time 300
```

**ジョブ投入**:
```bash
# アレイジョブで10ワーカーを投入
miqsub -t 1-10 qsub_worker.sh
```

### データベース管理

```bash
# CSV → SQLite
db_util import jobs.db input.csv

# SQLite → CSV（すべて）
db_util export jobs.db output.csv

# SQLite → CSV（完了したもののみ）
db_util export jobs.db done.csv --status done

# 統計表示
db_util stats jobs.db

# すべてのジョブをpendingにリセット
db_util reset jobs.db
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
```

## 予約カラム名

すべての予約カラムは`JOBSCHEDULER_`で始まります：

- `JOBSCHEDULER_JOB_ID` - ジョブの一意識別子
- `JOBSCHEDULER_STATUS` - ジョブのステータス（pending/running/done/error）
- `JOBSCHEDULER_PRIORITY` - 優先度（大きいほど先に実行）
- `JOBSCHEDULER_ESTIMATE_TIME` - 推定実行時間（時間単位）
- `JOBSCHEDULER_ELAPSED_TIME` - 実際の実行時間（秒単位）
- `JOBSCHEDULER_CREATED_AT` - 作成日時
- `JOBSCHEDULER_STARTED_AT` - 開始日時
- `JOBSCHEDULER_FINISHED_AT` - 終了日時
- `JOBSCHEDULER_ERROR_MESSAGE` - エラーメッセージ

## 動作の仕組み

### ジョブ実行フロー

1. **ジョブ選択**: `pending`状態のジョブを取得
   - `JOBSCHEDULER_PRIORITY`の降順でソート
   - `smart-scheduling=true`の場合、残り時間内に収まるジョブのみ選択

2. **ステータス更新**: `running`に変更、`JOBSCHEDULER_STARTED_AT`を記録

3. **コマンド実行**:
   - **位置引数モード**: `command param1 param2 param3 ...`
   - **名前付き引数モード**: `command --param1 value1 --param2 value2 ...`

4. **完了処理**: `done`または`error`に更新、`elapsed_time`を記録

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
job_scheduler jobs.db run.sh

# または手動でリセット
db_util reset jobs.db
```

### Q: ジョブが実行されない

```bash
# ステータス確認
db_util stats jobs.db

# estimate_timeが大きすぎて残り時間内に収まらない場合
# → smart-schedulingを無効化
job_scheduler jobs.db run.sh --smart-scheduling false
```

### Q: データベースロックエラー

- 通常は高競合時に発生し、自動的にリトライされます
- 持続する場合は、長時間実行中のトランザクションやロックをチェック

### Q: 並列実行しても速くならない

- `--parallel`は1ノード内の並列数です。複数ノード投入の方が効率的
- ジョブが軽すぎる（<1秒）場合はオーバーヘッドの影響が大きい
- 推奨：1ノード内は2-4並列程度、それ以上は複数ノード投入を推奨

## テスト

### 基本機能テスト

```bash
cd job-runner-v2

# 位置引数モード
db_util import test_basic/test_jobs.db test_basic/test_jobs.csv
job_scheduler test_basic/test_jobs.db test_basic/test_script.sh --max-runtime 60

# 名前付き引数モード
db_util reset test_basic/test_jobs.db
job_scheduler test_basic/test_jobs.db "tests/basic/test_script_named.py" --named-args --max-runtime 60
```

### 複数ノードテスト（TSUBAME）

```bash
# 10ワーカー × 200ジョブ
./run_multinode_test.sh

# 進捗監視
./tests/multinode/setup_multinode_test.py verify test_real_multinode.db

# 結果確認
./tests/multinode/setup_multinode_test.py verify test_real_multinode.db
```

### ストレステスト

```bash
# 1700ジョブ × 30ワーカー
./test_stress/run_stress_test.sh

# 結果確認
db_util stats test_stress/stress_1000.db
db_util stats test_stress/stress_500.db
db_util stats test_stress/stress_parallel.db
```


### ベンチマーク結果

| テスト | 構成 | 結果 | スループット |
|--------|------|------|--------------|
| ローカル | 8並列 × 100ジョブ | 100/100完了 | 42 jobs/sec |
| 複数ノード | 10ワーカー × 200ジョブ | 200/200完了 | データ整合性100% |
| 高負荷 | 20ワーカー × 500ジョブ | 494/500完了 | 98.8%成功率 |
| ストレス | 30ワーカー × 1700ジョブ | 1668/1700完了 | 98.1%成功率 |

※失敗ジョブは意図的なランダム失敗テストによるもの

## ライセンス

親ディレクトリのLICENSEファイルを参照してください。

## 開発状況

- ✅ Phase 0: SQLite動作確認
- ✅ Phase 1: 基本実装
- ✅ Phase 2: 柔軟な実行方式
- ✅ Phase 3: スケジューリング改善
- ✅ Phase 4: 並列実行モード
- ✅ Phase 5: 進捗ビューア
- ⏳ Phase 6: テスト・ドキュメント（進行中）

**本番環境で使用可能です！**

## 参考資料

- [QUICKSTART.md](QUICKSTART.md) - 5分でわかる使い方
- [SETUP.md](SETUP.md) - インストールとセットアップ
- [README_TEST.md](README_TEST.md) - テスト手順書
- [README.md](README.md) - English documentation
