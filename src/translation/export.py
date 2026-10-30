"""Export saved translations for editors; no authentication or model calls."""
import argparse
import csv
import json
from pathlib import Path
import pysubs2
from .captions import write_subtitles, wrap_text


def export_saved(source, output=None, bilingual=False, source_time=False):
    source = Path(source).expanduser().resolve()
    if source.is_dir():
        source = source/'translation.json'
    output = Path(output).expanduser().resolve() if output else source.parent/'exports'
    output.mkdir(parents=True,exist_ok=True)
    data = json.loads(source.read_text(encoding='utf-8'))
    manifest_path=source.parent/'manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.is_file() else {}
    if source_time and 'start_seconds' not in manifest:
        raise ValueError('元動画の開始時刻が記録されていません。通常のクリップ時刻で書き出せます。')
    offset=manifest.get('start_seconds',0) if source_time else 0
    cues=[{**c,'start':c['start']+offset,'end':c['end']+offset,
           'translation':c.get('translation') or c['text'],'translated':c.get('translated',False)} for c in data['cues']]
    write_subtitles(cues,output,bilingual=bilingual)
    subs=pysubs2.load(str(output/'subtitles.ass'))
    subs.save(str(output/'subtitles.vtt'))
    for name, dual in [('original.srt',False),('bilingual.srt',True)]:
        track=pysubs2.SSAFile()
        for cue in cues:
            text=cue['translation']+'\n'+cue['text'] if dual and cue['translated'] else cue['text']
            track.events.append(pysubs2.SSAEvent(start=round(cue['start']*1000),end=round(cue['end']*1000),text=wrap_text(text,25)))
        track.save(str(output/name))
    with (output/'transcript.tsv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream,delimiter='\t')
        writer.writerow(['字幕ID','開始秒','終了秒','話者','原文','訳文','翻訳済み'])
        for c in cues:
            cells=[c['id'],c['start'],c['end'],c.get('speaker',''),c['text'],c['translation'],c['translated']]
            writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v for v in cells])
    text='\n\n'.join(f"[{c['start']:.3f}〜{c['end']:.3f}秒 / {c.get('speaker') or '話者不明'}]\n原文: {c['text']}\n訳文: {c['translation']}" for c in cues)
    (output/'transcript.txt').write_text(text+'\n',encoding='utf-8')
    result={'source':str(source),'output':str(output),'cues':len(cues),'offset_seconds':offset,
            'timeline':'source_video' if source_time else 'selected_clip',
            'files':['subtitles.srt','subtitles.ass','subtitles.vtt','original.srt','bilingual.srt','transcript.tsv','transcript.txt'],
            'model_requests':0}
    (output/'export.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description='保存済み翻訳から字幕・対訳表を書き出します。API接続は不要です。')
    parser.add_argument('source',type=Path,help='translation.json または制作フォルダ')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--bilingual',action='store_true',help='標準字幕を原文・訳文の併記にする')
    parser.add_argument('--source-time',action='store_true',help='元動画の開始時刻を加える')
    args=parser.parse_args()
    try:
        result=export_saved(args.source,args.output,args.bilingual,args.source_time)
    except (OSError,ValueError,KeyError) as error:
        parser.exit(1,f'書き出しできませんでした: {error}\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
