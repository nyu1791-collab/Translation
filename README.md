# Translation

海外動画の音声を文字起こしし、元の発話時刻を使って日本語などの字幕と字幕付き動画を作るツールです。動画全編と、秒数で指定した範囲の両方に対応します。このリポジトリだけで導入・実行でき、他の制作リポジトリやそのPython環境は不要です。

## 基本の流れ

1. ローカル動画または公開動画URLを取得する。対応するURLはYouTube、Twitch、Kick、直接HTTPS動画。
2. Gemini 3.5 Transcribeで元の言語を自動判定して文字起こしし、単語時刻と話者ラベルを取得する。元言語は `--source-language en-US` のように指定もできる。
3. 15分未満の音声窓を最大6並列で処理する。長い有音空白は追加の文字起こしを最大2区間／窓まで試す。
4. 各発話にIDを付け、Gemini 3.5 Flash/MEDIUMで文脈を考慮して翻訳する。訳は元の発話IDと時刻へ結び付ける。モデルに新しい時刻を生成させない。
5. SRT・ASS・字幕入りMP4と `translation.json` を作る。`translation.json` は元文、訳文、時刻、窓内の話者ラベルを保持する。

元の単語と翻訳語の順序・長さは異なるため、字幕時刻は発話単位の推定であり、訳語の単語単位同期ではありません。話者ラベルは音声窓ごとに独立しています。RMS音量は発話を証明せず、空白補助がBGMを誤認することや小声を見逃すことがあります。訳抜けIDには元文を残して `translation.json` と `manifest.json` に記録し、状態を `partial` とします。時刻付き発話が一つも得られない場合は `no_timed_speech` と記録します。

## 使い始める

Python 3.11以上、FFmpeg（libass）、日本語フォント、Vertex AIが必要です。GPUは文字起こし・翻訳には不要です。動画描画はCPUが既定で、NVENCが使える環境では指定できます。

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
export GOOGLE_CLOUD_PROJECT='自分のGoogle CloudプロジェクトID'
gcloud auth application-default login
python -m translation 'https://www.youtube.com/watch?v=VIDEO_ID' --output ./output/example
```

Vertexの利用権限とモデル提供状況が必要です。完成物・キャッシュ・動画は出力ディレクトリに保管されます。公開リポジトリ内へ入れないでください。CLIはローカルファイルも使えます。

別のGoogle Cloudプロジェクトを使う場合、ローカルでは `--project プロジェクトID` を指定します。GitHub Actionsでは、各プロジェクトを一度接続設定してから `project_id` で選択できます。既存のプロジェクトの接続は維持します。[複数プロジェクトの接続手順](docs/SETUP.md#複数プロジェクトで使う)

```bash
# 途中の範囲だけ。時刻は元動画の秒数
python -m translation input.mp4 --output ./output/clip --start 180 --end 600 --source-language en-US

# 対訳を併記し、字幕だけ生成
python -m translation input.mp4 --output ./output/subtitles --bilingual --subtitles-only

# NVIDIA GPUが利用できる場合の描画
python -m translation input.mp4 --output ./output/gpu --encoder h264_nvenc
```

実行中に止まった場合は、同じ入力・設定・出力先で再実行すると、同一入力と設定の保存済みモデル応答を再利用します。完成済み結果が揃っていればそのまま返します。入力や設定が異なる場合は別の出力先を使います。

## 設定

`config.example.json` をコピーし `--config 設定.json` で指定できます。CLIで明示した値は設定ファイルより優先されます。

| 設定 | 既定 | 変更例 |
|---|---|---|
| 元の言語 | 自動判定 | `--source-language en-US`, `ko-KR`, `fr-FR` |
| 翻訳先 | 日本語 | `--target-language English` |
| 並列数 | 6 | `--workers 3` |
| Google Cloud | 環境変数またはActionsの既定プロジェクト | `--project プロジェクトID`、Actionsの `project_id` |
| 翻訳バッチ | 60発話 | `--batch-size 40` |
| 整文・翻訳 | `gemini-3.5-flash`／MEDIUM | `--thinking LOW` または `HIGH` |
| 出力 | 360p／CPU | `--height 720`、`--height 0` は元解像度、`--encoder h264_nvenc` |
| 字幕 | 翻訳のみ、1行25文字程度 | `--bilingual`、`--subtitles-only`、`--line-chars 20` |
| 空白補助 | 無効（追加調査なし） | 必要時だけ `--gap-recovery` |
| 素材取得上限 | 6 GiB・6時間 | `--max-bytes`、`--max-seconds` |

表示秒数・字幕幅などは目安です。目安未達を理由に制作を拒否する品質監査や自動再生成は行いません。HTTP 429・一時的なサーバー／通信エラーは同じモデル要求を合計3回まで再試行します。

## GitHub Actions

公開リポジトリ上のActionsでは、完成動画・字幕を公開Artifactに置かず、指定した**非公開**リポジトリのReleaseへ `result.zip` を保存します。Workflowの入力値は公開リポジトリの実行履歴から見えるため、URLには署名付きURLや認証情報を入れないでください。

初回接続には Google Cloud側のWIF設定、GitHub Actionsの変数、非公開保存先へ書けるトークンが必要です。既存ClipForgeのGoogle側の設定はそのリポジトリに限定されており、Translationから自動流用できるとは限りません。[接続手順](docs/SETUP.md) を参照してください。未設定のままではActionsは完了しません。

ローカルで描画している場合は、`result.zip` の自動生成は行わず、出力ディレクトリに各ファイルを残します。Driveへ直接納品する機能とColab GPUの自動確保は、このリポジトリでは未実装です。

## 外部プロジェクトと資料

- [pysubs2](https://github.com/tkarabela/pysubs2)（MIT）：SRT・ASSの作成ライブラリ。
- [BudouX](https://github.com/google/budoux)（Apache-2.0）：日本語の読みやすい文の折り返し。
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)（Unlicense）：対応サイトのメディア取得。同梱の素材取得部品から利用。
- [subtitle-translator](https://github.com/dhofverberg/subtitle-translator)（MIT）：字幕のバッチ翻訳と文脈保持の参考。コードはコピーしていません。
- [Gemini Transcribe公式仕様](https://ai.google.dev/gemini-api/docs/generate-content/transcribe)：言語の自動判定・時刻・話者分離の設定。
- [VertexのTranscribe仕様](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-5-transcribe)：単語時刻・話者分離時の音声長制限。

`src/translation/_media` の素材取得等は同じ所有者の [ClipForge](https://github.com/nyu1791-collab/ClipForge) のコミット `dd994d0` からコピーしています。


制作の目標・再開・接続の整理は [制作経路の整理](docs/STREAMLINING.md) を参照してください。

保存済み翻訳からSRT・ASS・VTT・原文字幕・対訳表を書き出す方法は [字幕と対訳の書き出し](docs/EXPORT.md)、人名や専門用語の表記指定は [用語集](docs/GLOSSARY.md) を参照してください。

## 書き出し品質

字幕付き動画は `src/translation/ffmpeg_recipes.py` の設定で書き出します（lanczos縮小、BT.709、AAC 192k/48kHz、faststart）。`--quality draft|standard|high` で選び、既定は standard です。`--preset` を付けた場合は x264 の preset だけを上書きします。

## 関連リポジトリ

制作パイプライン全体の構成と各リポジトリの役割は [ClipForge の README](https://github.com/nyu1791-collab/ClipForge#関連リポジトリ) を参照してください。
