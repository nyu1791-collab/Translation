# 保存済み翻訳を編集・納品へ渡す

翻訳済みの `translation.json` から、モデルを呼び直さずに字幕と対訳表を出力します。認証やプロジェクトIDは不要です。

```powershell
translation-export C:/work/translated-video
# インストール済みコマンドを使わない場合
python -m translation.export C:/work/translated-video
```

既定の出力先は制作フォルダの `exports`。元の動画・翻訳JSON・字幕は変更しません。

| 出力 | 用途 |
|---|---|
| subtitles.srt / subtitles.ass / subtitles.vtt | 翻訳字幕。通常の編集ソフトやWeb動画向け |
| original.srt | 原文の字幕 |
| bilingual.srt | 原文と訳文を併記した字幕 |
| transcript.tsv | 時刻・話者・原文・訳文を表計算ソフトで扱う対訳表 |
| transcript.txt | 人が読める対訳テキスト |
| export.json | 時間軸・出力ファイル・元データの記録 |

`--bilingual` を付けると標準のsubtitles系も対訳表示にできます。元の字幕時刻と話者ラベルを使い、翻訳後の単語時刻を生成する処理はありません。翻訳されなかった箇所は元の表現を保持します。

## 元動画の時間軸に戻す

```powershell
translation-export C:/work/translated-video --source-time --output C:/work/source-timeline-subtitles
```

たとえば元動画の120秒から制作した場合、クリップ内0.2秒の字幕を元動画内120.2秒へ配置します。開始時刻は元の `manifest.json` を使います。開始記録がなければ推測して補いません。既定はクリップ内の時間軸です。

## 字幕編集ソフトへの受け渡し

Subtitle EditなどでSRT・ASS・VTTを開き、文面を編集できます。書き出した字幕だけでは動画に焼き込まれた字幕は変わりません。

制作フォルダの動画と、同じ時間軸の字幕を編集ソフトへ渡してください。`--source-time` の字幕は元動画用なので、切り出し済み動画用の字幕と混ぜないでください。

## 参考元

- [Subtitle Edit](https://github.com/SubtitleEdit/subtitleedit): 字幕を専用の編集ソフトで扱い、複数形式で受け渡す考え方を参考にしました。コードやアプリ本体を複製・組み込みしたものではありません。
- [pysubs2](https://github.com/tkarabela/pysubs2): 既存依存の字幕ライブラリを継続使用し、SRT・ASS・VTTの書き出しに利用しています。

この機能に品質監査、追加モデル、映像の全デコード検査はありません。
