#!/usr/bin/env python3
"""Normal typed-chapter burn; legacy tool remains byte-for-byte frozen for old evidence."""
import argparse,json,os,shutil,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.burn_subtitles import sha,font_coverage_preflight,declared_font_fallback
from storyflow.captions import validate_srt
from storyflow.caption_mask import validate_mask,caption_filter,notice_filter

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
    p.add_argument('--frame-mask', required=True, type=Path)
    p.add_argument('--font', required=True, type=Path)
    p.add_argument('--font-family', required=True)
    p.add_argument('--font-index', type=int, default=0)
    a = p.parse_args()
    os.environ['STORY_SUBTITLE_FONT_INDEX']=str(a.font_index)
    video, srt, out, work = (x.expanduser().resolve() for x in (a.video, a.srt, a.output, a.work))
    mask_path=a.frame_mask.expanduser().resolve()
    mask=validate_mask(json.loads(mask_path.read_text()),video,probe=True)
    mask_sha256=sha(mask_path)
    font = a.font.expanduser().resolve()
    if not font.is_file():
        raise SystemExit('实际指定字体不存在；拒绝fallback')
    family = a.font_family
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
    vf=caption_filter(snapshot,font,style,mask,a.first_frame_cover)
    if a.illustrated_reconstruction:
        vf += notice_filter(font,a.first_frame_cover)
    command = ['ffmpeg', '-nostdin', '-hide_banner', '-nostats', '-loglevel', 'info', '-i', str(video), '-map', '0:v:0', '-map', '0:a:0', '-vf', vf, '-af', 'asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0', '-c:v', 'libx264', '-preset', 'fast', '-profile:v', 'high', '-bf', '0', '-b:v', f'{bitrate//1000}k', '-maxrate', f'{max(int(bitrate*1.5),bitrate+100000)//1000}k', '-bufsize', f'{max(int(bitrate*2),bitrate+200000)//1000}k', '-r', '30', '-video_track_timescale', '15360', '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-b:a', '192k', '-movflags', '+faststart', '-avoid_negative_ts', 'disabled', '-use_editlist', '1']
    if a.sample_seconds:
        command += ['-t', str(a.sample_seconds)]
    command += [str(out)]
    receipt = {'caption_quality': caption_quality, 'first_frame_cover': a.first_frame_cover, 'frame_mask':mask, 'frame_mask_path':str(mask_path), 'frame_mask_sha256':mask_sha256, 'legacy_helper_sha256':sha(Path(__file__).with_name('burn_subtitles.py')), 'caption_mask_helper_sha256':sha(Path(__file__).resolve().parents[1]/'storyflow/caption_mask.py'), 'font_coverage_preflight': coverage, 'video': str(video), 'video_sha256': sha(video), 'srt': str(srt), 'srt_sha256': sha(srt), 'font': str(font), 'font_sha256': sha(font), 'force_style': style, 'srt_offset_seconds': 0, 'preset': 'fast', 'command': command}
    work.mkdir(parents=True, exist_ok=True)
    if a.check_existing:
        old = json.loads((work / 'command.json').read_text())
        if not out.is_file() or any(old.get(k) != receipt[k] for k in ('command', 'video_sha256', 'srt_sha256', 'font_sha256','frame_mask_sha256','legacy_helper_sha256','caption_mask_helper_sha256')):
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
    if sha(mask_path)!=mask_sha256:raise SystemExit('caption mask changed during burn')
    validate_mask(mask,video)
    if sha(Path(__file__).with_name('burn_subtitles.py'))!=receipt['legacy_helper_sha256'] or sha(Path(__file__).resolve().parents[1]/'storyflow/caption_mask.py')!=receipt['caption_mask_helper_sha256']:raise SystemExit('caption mask helper changed during burn')
    receipt.update(output=str(out), output_sha256=sha(out), font_select=selected, output_bytes=out.stat().st_size)
    receipt['declared_glyph_fallback'] = fallback_info
    (work / 'result.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()
