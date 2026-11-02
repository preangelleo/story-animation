#!/usr/bin/env python3
"""额外审阅版：Agent J noir 字幕 + MacroAlpha ultrasafe，不写正式artifact。"""
import argparse
import os
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unicodedata
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storyflow.captions import validate_srt


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


FALLBACK_FONT = Path('/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc')
FALLBACK_SHA256 = 'b76b0433203017ca80401b2ee0dd69350349871c4b19d504c34dbdd80541690a'
DECLARED_GLYPHS = {'0x3007': 'U+3007 〇', '0x71CA': 'U+71CA 燊', '0x9245': 'U+9245 鉅'}

def declared_font_fallback(selected, missing, family):
    """Permit only the explicitly declared observed missing characters in a pinned local CJK font."""
    fallback = FALLBACK_FONT
    known = DECLARED_GLYPHS
    pinned_sha256 = FALLBACK_SHA256
    fields = lambda line: [part.strip() for part in line.split(' -> ', 1)[-1].split(',')]
    permitted = [x for x in selected if fields(x) == [str(fallback), '1', 'NotoSansCJKkr-Regular']]
    primary = [x for x in selected if fields(x) == [family, os.environ.get('STORY_SUBTITLE_FONT_INDEX','0'), family]]
    observed = [code for code in known if any('Glyph '+code+' not found' in line for line in missing)]
    allowed_missing = [line for line in missing if any('Glyph '+code+' not found' in line for code in known)]
    if not primary or len(allowed_missing) != len(missing) or len(primary) + len(permitted) != len(selected) or bool(missing) != bool(permitted):
        raise SystemExit('字体选择不符或出现未声明缺字fallback；输出不得交付')
    if not permitted:
        return None
    if not fallback.is_file() or sha(fallback) != pinned_sha256:
        raise SystemExit('声明的补字字体实体SHA不符；输出不得交付')
    return {'glyph': ', '.join(known[c] for c in observed), 'glyphs': [known[c] for c in observed], 'font_file': str(fallback), 'font_sha256': sha(fallback), 'face': 'NotoSansCJKkr-Regular', 'reason': '主字体缺字，沿用libass实际补字；仅允许明确声明的〇、燊与鉅，字形须独立实审；其他缺字仍拒绝'}


def font_coverage_preflight(font, srt):
    """Ask installed fontconfig for coverage before encoding; do not parse fonts."""
    from storyflow.vendor.smart_caption.convert import read_srt
    def charset(path, index):
        try:
            output=subprocess.check_output(['fc-query','--index',str(index),'--format','%{postscriptname}\n%{charset}',str(path)],text=True)
            face,raw=output.split('\n',1)
            ranges=[]
            for token in raw.split():
                ends=token.split('-');low=int(ends[0],16);high=int(ends[-1],16)
                if len(ends)>2 or low>high:raise ValueError('invalid charset range')
                ranges.append((low,high))
            if not ranges:raise ValueError('empty fontconfig charset')
            return face,lambda code:any(low<=code<=high for low,high in ranges)
        except (OSError,subprocess.CalledProcessError,ValueError) as exc:
            raise SystemExit('fc-query字体字符集预检失败，拒绝开始编码') from exc
    _,primary=charset(font,int(os.environ.get('STORY_SUBTITLE_FONT_INDEX','0')))
    used={ord(char) for cue in read_srt(str(srt)) for char in cue['text'] if not char.isspace() and unicodedata.category(char)[0]!='C'}
    missing=sorted(code for code in used if not primary(code))
    allowed={int(code,16) for code in DECLARED_GLYPHS}
    undeclared=[f'U+{code:04X}' for code in missing if code not in allowed]
    if undeclared:raise SystemExit('编码前发现未声明缺字：'+', '.join(undeclared))
    if missing:
        if not FALLBACK_FONT.is_file() or sha(FALLBACK_FONT)!=FALLBACK_SHA256:raise SystemExit('声明的补字字体实体SHA不符；拒绝开始编码')
        face,covered=charset(FALLBACK_FONT,1)
        if face!='NotoSansCJKkr-Regular' or any(not covered(code) for code in missing):raise SystemExit('声明补字face/字符覆盖不符；拒绝开始编码')
    return {'method':'installed fc-query charset','missing_from_primary':[f'U+{code:04X}' for code in missing],'fallback_face':'NotoSansCJKkr-Regular' if missing else None,'fallback_font_sha256':FALLBACK_SHA256 if missing else None,'scope':'coverage only; actual glyph shape and libass font selection require independent review and postcheck'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--video', required=True, type=Path)
    p.add_argument('--srt', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--work', required=True, type=Path)
    p.add_argument('--sample-seconds', type=float)
    p.add_argument('--check-existing', action='store_true', help='只核已存在本次同命令导出，不重新编码')
    p.add_argument('--execute', action='store_true')
    p.add_argument('--first-frame-cover', action='store_true', help='Keep frame zero free of all caption/notice overlays; original cue windows resume on frame one')
    p.add_argument('--illustrated-reconstruction', action='store_true', help='片头5秒注明真实事件改编、部分演绎与卡通画面，不改变旁白和字幕')
    a = p.parse_args()
    video, srt, out, work = (x.expanduser().resolve() for x in (a.video, a.srt, a.output, a.work))
    font = Path(os.environ.get('STORY_SUBTITLE_FONT','assets/fonts/subtitle.ttf')).expanduser().resolve()
    if not font.is_file():
        raise SystemExit('实际指定字体不存在；拒绝fallback')
    family = os.environ.get('STORY_SUBTITLE_FAMILY','AidianFengYaHeiChangTi')
    matched = subprocess.check_output(['fc-match', '-f', '%{file}', family], text=True).strip()
    if Path(matched).resolve() != font.resolve():
        raise SystemExit('fontconfig没有选中指定TTF；拒绝fallback')
    if (out.exists() and not a.check_existing) or out == video:
        raise SystemExit('输出存在或等于源视频，拒绝覆盖')
    if not video.is_file() or not srt.is_file():
        raise SystemExit('源文件缺失')
    caption_quality = validate_srt(srt)
    coverage = font_coverage_preflight(font, srt)
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=bit_rate', '-of', 'json', str(video)], text=True))
    bitrate = max(int(probe['streams'][0]['bit_rate']), 300000)
    style = f'FontName={family},Fontsize=20,PrimaryColour=&H00FFFFFF,BorderStyle=3,Outline=6,Shadow=0,BackColour=&H0D141414,OutlineColour=&H0D141414,MarginV=16,Alignment=2'
    snapshot = work / 'subtitles.srt'
    vf = f"setpts=PTS-STARTPTS,fps=30,format=yuv420p,subtitles=filename='{snapshot}':fontsdir='{font.parent}':force_style='{style}'"
    if a.first_frame_cover:
        # subtitles/libass has no timeline enable support: split, overlay only its
        # rendered result from frame one; the untouched branch supplies frame zero.
        base="setpts=PTS-STARTPTS,fps=30,format=yuv420p"
        caption=vf.split('format=yuv420p,',1)[1]
        vf=base+",split[plain][caption];[caption]"+caption+"[sub];[plain][sub]overlay=enable='gte(n,1)':format=yuv420"
    if a.illustrated_reconstruction:
        notice_enable='between(t,0.03333333333333333,5)' if a.first_frame_cover else 'lt(t,5)'
        vf += f",drawtext=fontfile='{font}':text='真实事件改编  部分情节演绎  卡通画面':fontsize=28:fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=10:x=48:y=36:enable='{notice_enable}'"
    command = ['ffmpeg', '-nostdin', '-hide_banner', '-nostats', '-loglevel', 'info', '-i', str(video), '-map', '0:v:0', '-map', '0:a:0', '-vf', vf, '-af', 'asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0', '-c:v', 'libx264', '-preset', 'fast', '-profile:v', 'high', '-bf', '0', '-b:v', f'{bitrate//1000}k', '-maxrate', f'{max(int(bitrate*1.5),bitrate+100000)//1000}k', '-bufsize', f'{max(int(bitrate*2),bitrate+200000)//1000}k', '-r', '30', '-video_track_timescale', '15360', '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-b:a', '192k', '-movflags', '+faststart', '-avoid_negative_ts', 'disabled', '-use_editlist', '1']
    if a.sample_seconds:
        command += ['-t', str(a.sample_seconds)]
    command += [str(out)]
    receipt = {'caption_quality': caption_quality, 'first_frame_cover': a.first_frame_cover, 'font_coverage_preflight': coverage, 'video': str(video), 'video_sha256': sha(video), 'srt': str(srt), 'srt_sha256': sha(srt), 'font': str(font), 'font_sha256': sha(font), 'force_style': style, 'srt_offset_seconds': 0, 'preset': 'fast', 'command': command}
    work.mkdir(parents=True, exist_ok=True)
    if a.check_existing:
        old = json.loads((work / 'command.json').read_text())
        if not out.is_file() or any(old.get(k) != receipt[k] for k in ('command', 'video_sha256', 'srt_sha256', 'font_sha256')):
            raise SystemExit('现有导出与原命令/输入不符')
    else:
        (work / 'command.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    if not a.execute:
        print(json.dumps(receipt, ensure_ascii=False))
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    if not a.check_existing:
        shutil.copyfile(srt, snapshot)
    assert sha(snapshot) == receipt['srt_sha256']
    if not a.check_existing:
        with (work / 'ffmpeg.log').open('w') as log:
            subprocess.run(command, stdout=log, stderr=log, check=True)
    log = (work / 'ffmpeg.log').read_text()
    selected = [x for x in log.splitlines() if 'fontselect:' in x]
    missing = [x for x in log.splitlines() if 'not found, selecting' in x]
    fallback_info = declared_font_fallback(selected, missing, family)
    if sha(video) != receipt['video_sha256'] or sha(srt) != receipt['srt_sha256']:
        raise SystemExit('源文件变动')
    receipt.update(output=str(out), output_sha256=sha(out), font_select=selected, output_bytes=out.stat().st_size)
    receipt['declared_glyph_fallback'] = fallback_info
    (work / 'result.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()
