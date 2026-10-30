# 初回接続と実行

## Google Cloud / GitHub Actions

GitHub ActionsからGoogle Vertexへの接続はOIDCを使用し、APIキーやサービスアカウント鍵をリポジトリに置きません。このリポジトリ専用のWIFプール・サービスアカウントを作るため、既存ClipForgeの設定は変更しません。

1. 使用するGoogle Cloudプロジェクトの所有者としてCloud Shellを開く。
2. このリポジトリを取得し、使いたいプロジェクトIDを明示して `GOOGLE_CLOUD_PROJECT=プロジェクトID bash scripts/setup_vertex_wif.sh` を実行する。既定のリポジトリは `nyu1791-collab/Translation`、既定プロジェクトはユーザーの既存Vertexプロジェクト。
3. スクリプトは指定プロジェクトに `translation-actions` WIFプールと `translation-vertex` サービスアカウントを作り、Translationの `main` だけを許可する。ユーザーの指定により、専用サービスアカウントへそのプロジェクトの編集者（`roles/editor`）を付与する。プロジェクト内の多くのリソースを変更できる権限であり、他のユーザーへIAM権限を与える権限やGitHub・Driveへの権限を意味しない。
4. Repository Settings → Secrets and variables → Actions で、非公開の保存先リポジトリを読み書きできる `TRANSLATION_STORAGE_TOKEN` を登録する。既存の別リポジトリのSecret値はGitHubから読み戻せないため、手動登録または組織Secretの付与が必要。YouTube認証が必要な素材に限り `YOUTUBE_COOKIES_B64` も登録する。
5. Actions → Translation video → Run workflow から、公開動画URLと必要なオプションを入力する。`translation-result-RUN_ID` のReleaseが非公開保存先に作成される。

スクリプトは、認証済みの `gh` が変数を読み書きできる場合、プロジェクトごとの接続先を `TRANSLATION_PROJECTS` に追加する。既存の別プロジェクトの項目と既定値は保持する。`gh` が使えない場合は追加するJSONの項目を表示するので、Repository Settings → Secrets and variables → Actions のVariablesに登録する。既存の `TRANSLATION_WIF_PROVIDER` と `TRANSLATION_SERVICE_ACCOUNT` は既定プロジェクト用として引き続き利用できる。元の既定プロジェクトについては、確認済みのWIFを変数未登録時の既定値として使用する。

## 複数プロジェクトで使う

各プロジェクトごとに、その所有者のCloud Shellから次を実行する。既存の `Translation` フォルダ内なら `git pull` からでよい。

```bash
git pull
GOOGLE_CLOUD_PROJECT=別のプロジェクトID bash scripts/setup_vertex_wif.sh
```

Actionsの `project_id` に登録済みのIDを入力すると、そのプロジェクトのWIFとサービスアカウントで接続し、同じIDのVertexへ要求する。空欄なら `TRANSLATION_PROJECT_ID` または従来の既定値を使う。`TRANSLATION_SET_DEFAULT=1` を併せて設定スクリプトに渡すと、登録成功時にそのプロジェクトを既定にできる。`project_id` にはIDを指定し、プロジェクト番号は入力しない。未登録のプロジェクトへは別の認証先を勝手に流用しない。

ローカルでは、権限のあるApplication Default Credentialsを用意し `python -m translation 動画 --output 出力先 --project プロジェクトID` と指定できる。各プロジェクトでVertex AI APIの有効化・課金・モデル提供・IAMの確認が必要で、設定スクリプトは課金を有効にしない。API要求は指定プロジェクトに送られ、そのプロジェクトの設定・利用状況に従う。

GitHub Actionsの保存用トークンは保存先が非公開か確認したうえでReleaseを作成する。動画・文字起こし・翻訳・字幕は公開リポジトリのActions artifactへは保存しない。

## 注意点と再開

- WIFの条件はRepositoryの名前と `refs/heads/main` に限定している。ブランチから手動実行する場合は許可されない。
- Actionsの実行時間、GPU、音声認識精度、翻訳や同期は保証されない。Actionsの既定描画はCPU。
- ローカルではGoogle CloudのApplication Default Credentialsか、短命の `TRANSLATION_VERTEX_ACCESS_TOKEN` を使用する。トークンやCookieを引数・ログ・Gitに出さない。
- 同じ入力・設定では保存済みのモデル応答を再利用できる。Actionsは途中の応答と使用量記録を非公開Release `translation-checkpoints-RUN_ID` に保存する。別Runの `resume_run_id` に元のRun IDを明示すると、対応するキャッシュを復元する。素材自体は再取得するため、入力の識別が一致する応答だけが再利用される。失敗時にチェックポイント保存自体が失敗した場合は復元できない。
- 対象動画を利用する権限やプラットフォームの取得条件は利用者が確認する。

## 作業環境からの接続確認

各プロジェクトでWIFの初期設定を完了した後、Actionsの `Verify Google Cloud connection` にプロジェクトIDとプロジェクト番号を指定すると、Translation専用サービスアカウントによる接続と、Vertex AI APIの有効化状態を読み取りで確認できる。番号は `gcloud projects describe プロジェクトID --format='value(projectNumber)'` で確認する。認証コード・アクセストークン・サービスアカウント鍵を入力しない。

これはGitHub Actions上の一時的な実行環境であり、ユーザーのCloud Shellへ直接ログインする機能ではない。既定ではプロジェクトIAMやAPI設定を変更しない。`enable_resource_manager` を明示した場合に限り、Cloud Resource Manager APIを有効化してから確認する。認証の成功やAPI有効化はモデルの提供状況・課金・実際の推論成功を保証しない。
