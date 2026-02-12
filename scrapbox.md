Job Scheduler v2
#tsubame4
2026/2/10更新

[kfurui.icon] [JobScheduler]のsqlite3使った安全版（開発中）
GitHubリポジトリ https://github.com/YumizSui/job-scheduler-v2

SQLiteベースの並列ジョブスケジューラ（TSUBAME等のqsub向け）

[*** できること]
	CSVから大量のジョブを一括実行
	ジョブ依存関係（DAG形式でジョブAとBが完了後にジョブCを実行）
	優先度付きスケジューリング
	マルチノード並列実行（qsubアレイジョブ対応）
	進捗のリアルタイム監視（依存状態も表示）
	中断しても自動復旧（実行中のジョブは保護）
	失敗したジョブだけ再実行
	実行中に追加ワーカーを投入可能

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

	6. 結果をエクスポート
		`db_util export experiments.db` (自動的に experiments.csv にエクスポート)
		`db_util export experiments.db done.csv --status done` (完了のみ)
		`db_util export experiments.db error.csv --status error` (失敗のみ)

[*** よくある使い方]
	大量の実験パラメータを試す
		code:bash
		 db_util import experiments.csv
		 miqsub -t 1-20 worker.sh  # 20ワーカーで並列実行
		 watch -n 5 'progress_viewer experiments.db'

	失敗したジョブだけリトライ
		code:bash
		 # エラージョブのみリセット
		 db_util reset experiments.db --status error
		 job_scheduler experiments.db "bash run.sh"

	既存DBにジョブを追加
		code:bash
		 db_util add experiments.db new_jobs.csv

	時間制約のあるジョブ（24時間以内、最後5分はマージン）
		`job_scheduler jobs.db "bash run.sh" --max-runtime 86400 --margin-time 300`

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

