Job Scheduler v2
#tsubame4
2026/3/25更新

[kfurui.icon] [JobScheduler]のsqlite3使った安全版（開発中）
GitHubリポジトリ https://github.com/YumizSui/job-scheduler-v2

SQLiteベースの並列ジョブスケジューラ（TSUBAME等のqsub向け）

[*** できること]
	CSVから大量のジョブを一括実行
	ジョブ依存関係（DAG形式でジョブAとBが完了後にジョブCを実行）
	優先度付きスケジューリング
	マルチノード並列実行（qsubアレイジョブ対応）
	進捗のリアルタイム監視（依存状態も表示）
	中断しても自動復旧（progress_viewer・db_util stats実行時にも復旧。実行中のジョブは保護）
	失敗したジョブだけ再実行
	実行中に追加ワーカーを投入可能
	実行中のジョブをIDで強制終了（errorステータスへ）

[*** 基本的な使い方]
	1. セットアップ
		code:bash
		 git clone https://github.com/YumizSui/job-scheduler-v2.git
		 cd job-scheduler-v2
		 chmod +x script/job_scheduler script/db_util script/progress_viewer
		 export PATH="$(pwd)/script:$PATH"
		 echo 'export PATH="/path/to/job-scheduler-v2/script:$PATH"' >> ~/.bashrc

	2. ジョブCSVを用意（param1,param2が実行時のパラメータ）
		code:csv
		 param1,param2,JOBSCHEDULER_PRIORITY,JOBSCHEDULER_ESTIMATE_TIME,JOBSCHEDULER_DEPENDS_ON
		 preprocess,data1,5,0.5,
		 training,model1,3,2.0,preprocess
		 evaluation,results,1,0.3,training

		→ 実際に投げられるジョブ
			位置引数モード（デフォルト）：
				`bash run.sh preprocess data1`
				`bash run.sh training model1`（preprocessが完了後）
				`bash run.sh evaluation results`（trainingが完了後）

			名前付き引数モード（--named-args）：
				`bash run.sh --param1 preprocess --param2 data1`
				`bash run.sh --param1 training --param2 model1`（preprocessが完了後）
				`bash run.sh --param1 evaluation --param2 results`（trainingが完了後）

	3. SQLiteにインポート
		`db_util import experiments.csv` (自動的に experiments.db 作成)

	4. ジョブを実行
		`job_scheduler experiments.db "bash run.sh"` (シングルノード)
		`job_scheduler experiments.db "bash run.sh" --parallel 4` (4並列、依存も自動管理)
		`miqsub -t 1-10 worker.sh` (複数ノード、10ワーカー)

	5. 進捗確認
		`progress_viewer experiments.db --watch`
		→ Ready/Waiting/Blocked の状態も表示
		→ stuckジョブ（heartbeat2分超）を自動リカバリ（`--no-recover`で無効化）

	6. ジョブを調べる
		`db_util show job_00000003 --db-path experiments.db`   (単一ジョブの全カラム)
		`db_util list --db-path experiments.db --status error --grep-error "CUDA"` (error絞り込み)
		`db_util stats experiments.db --by worker`   (worker別集計)
		`job_tui experiments.db`   (インタラクティブTUI、/でフィルタ、Enterで詳細)

	7. 結果をエクスポート（機械処理・他ツール連携用）
		`db_util export experiments.db` (自動的に experiments.csv にエクスポート)
		`db_util export experiments.db done.csv --status done` (完了のみ)

[*** よくある使い方]
	大量の実験パラメータを試す
		code:bash
		 db_util import experiments.csv
		 miqsub -t 1-20 worker.sh  # 20ワーカーで並列実行
		 watch -n 5 'progress_viewer experiments.db'

	失敗したジョブだけリトライ
		code:bash
		 # エラー内容を確認してからリセット
		 db_util list --db-path experiments.db --status error --grep-error "."
		 db_util show job_00000000 --db-path experiments.db
		 # エラージョブのみリセット
		 db_util reset experiments.db --status error
		 # 特定ジョブのみリセット
		 db_util reset experiments.db --jobs job_00000000,job_00000001
		 job_scheduler experiments.db "bash run.sh"

	特定ジョブを優先実行（完了後は通常スケジューリング）
		`job_scheduler experiments.db "bash run.sh" --jobs job_00000001,job_00000002`

	特定ジョブのみ実行して終了
		`job_scheduler experiments.db "bash run.sh" --jobs job_00000001,job_00000002 --jobs-only`

	ジョブをdone/errorに直接設定（外部で完了済みとしてスキップする場合など）
		code:bash
		 db_util reset experiments.db --set-status done --jobs job_00000005
		 db_util reset experiments.db --set-status error --status running

	既存DBにジョブを追加
		code:bash
		 db_util add experiments.db new_jobs.csv

	実行中のジョブを強制終了
		code:bash
		 db_util kill experiments.db --jobs job_00000042
		# schedulerが次のheartbeat（デフォルト30秒）で検知してerrorにする


	時間制約のあるジョブ（24時間以内、最後5分はマージン）
		`job_scheduler jobs.db "bash run.sh" --max-runtime 86400 --margin-time 300`

	推定実行時間が長いジョブを優先（LPT戦略、smart-schedulingと併用可）
		`job_scheduler jobs.db "bash run.sh" --longest-first`
		同一優先度内でESTIMATE_TIME降順に取得。長いジョブを先に流して短いジョブで隙間を埋める。

	ログ出力をstderrに切り替える（デフォルトはstdout）
		`job_scheduler jobs.db "bash run.sh" --log-stderr`

	実行中にワーカーを追加
		code:bash
		 # すでに10ワーカーが動いていても、追加で5ワーカー投入OK
		 # 実行中のジョブは保護される
		 miqsub -t 11-15 worker.sh

	qsub用のワーカースクリプト例
		code:bash
		 #!/bin/bash
		 #$ -cwd
		 #$ -l cpu_4=1
		 #$ -l h_rt=24:00:00
		 source $HOME/.bashrc
		 job_scheduler /path/to/jobs.db "bash run.sh" --max-runtime 86000 --margin-time 300

[*** 依存関係の使い方]
	CSVで依存関係を指定（スペース区切り）
		code:csv
		 JOBSCHEDULER_JOB_ID,task,JOBSCHEDULER_DEPENDS_ON
		 jobA,preprocess,
		 jobB,load_data,
		 jobC,training,jobA jobB
		 jobD,evaluation,jobC

	動作
		jobAとjobBが並列実行
		両方完了後、jobCが実行
		jobC完了後、jobDが実行
		依存ジョブがerrorの場合、jobは永久ブロック（スケジューラは自動停止）

	進捗確認
		code:bash
		 progress_viewer jobs.db
		 # Pending: 10
		 #   - Ready: 2      ← すぐ実行可能
		 #   - Waiting: 7    ← 依存ジョブ待ち
		 #   - Blocked: 1    ← エラーでブロック

