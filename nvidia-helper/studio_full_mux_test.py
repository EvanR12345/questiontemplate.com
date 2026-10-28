"""Full-story one-pass mux keeps media timing and resumable output guards."""
import copy,hashlib,json,math,os,shutil,struct,subprocess,tempfile,unittest,wave
from pathlib import Path
from unittest.mock import patch
from studio_data import ProjectStore,new_project,new_chapter,digest
from studio_render import VideoRenderer


class PreparedChapters(VideoRenderer):
    def __init__(self,*args):super().__init__(*args);self.calls=[]
    def chapter(self,p,ch,gate):
        return {'path':f'chapter-{ch["number"]}.mp4','duration':ch['audio']['duration'],'signature':str(ch['number'])}
    def run(self,args,gate,log,multiple_outputs=False):
        self.calls.append(list(args))
        return super().run(args,gate,log,multiple_outputs)


def fixture(root,ffmpeg=None,fps=30,audio_rate=24000):
    store=ProjectStore(root);p=new_project('Mux fixture')
    p['chapters']=[new_chapter(1),new_chapter(2)]
    p['settings']['video'].update(width=1280,height=720,fps=fps,motionMode='static')
    p['settings']['watermark']={'enabled':False}
    p['settings']['engagement'].update(popupEnabled=False,outroEnabled=False,splitEnabled=False)
    p['intro']['enabled']=False
    folder=store.folder(p['id']);folder.mkdir()
    for i,ch in enumerate(p['chapters'],1):
        ch.update(sourceText='Fixture narration.',cleanNarrationText='Fixture narration.',scenes=[],
                  audio={'path':f'audio-{i}.wav','duration':.6})
        with wave.open(str(folder/ch['audio']['path']),'wb') as wav:
            wav.setparams((1,2,audio_rate,0,'NONE','not compressed'))
            wav.writeframes(b''.join(struct.pack('<h',int(3000*math.sin(2*math.pi*(300+i*130)*n/audio_rate))) for n in range(round(.6*audio_rate))))
        video=folder/f'chapter-{i}.mp4'
        if ffmpeg:
            subprocess.run([ffmpeg,'-hide_banner','-v','error','-nostdin','-y',
                '-f','lavfi','-i',f'color=c={"red" if i==1 else "blue"}:s=1280x720:r={fps}',
                '-i',str(folder/ch['audio']['path']),'-t','0.6','-c:v','libx264','-preset','veryfast',
                '-crf','21','-threads','1','-bf','2','-c:a','aac','-b:a','128k',str(video)],check=True,capture_output=True,timeout=30)
        else:video.write_bytes(b'completed chapter '+bytes([i]))
    p=store.save(p)
    return store,p,PreparedChapters(store,{'ffmpeg':ffmpeg or ''})


def story_path(store,p):
    name=digest({'fullRendererVersion':2,'files':[f'chapter-{c["number"]}.mp4' for c in p['chapters']],
                 'video':p['settings']['video']})[:12]
    return store.folder(p['id'])/f'story-{name}.mp4'


class FullMuxCommandTest(unittest.TestCase):
    @staticmethod
    def encoded(args,*_):Path(args[-1]).write_bytes(b'completed story')

    def test_fresh_assembly_is_one_atomic_video_copy_and_keeps_duration_overrides(self):
        with tempfile.TemporaryDirectory() as root:
            store,p,renderer=fixture(root)
            p['chapters'][0]['audio']['duration']=.617;p=store.save(p)
            with patch.object(renderer,'run',side_effect=self.encoded) as run:
                result=renderer.full(p,lambda *_:None)
            self.assertEqual(run.call_count,1)
            args=run.call_args.args[0]
            self.assertEqual(args[:4],['-f','concat','-safe','0'])
            self.assertEqual(args[args.index('-c:v')+1],'copy')
            self.assertEqual(args[args.index('-bsf:v')+1],'setts=pts=PTS-STARTPTS:dts=DTS-STARTPTS')
            self.assertEqual(args[args.index('-t')+1],'1.217')
            self.assertIn('duration 0.617',Path(args[5]).read_text())
            self.assertFalse(list(store.folder(p['id']).glob('*-visual.mp4')))
            self.assertTrue(store.asset(p['id'],result['path']).is_file())
            self.assertEqual(result['fullAssemblyPolicy'],'direct-concat-original-audio-v1')
            self.assertFalse(result['reused'])
            with patch.object(renderer,'run') as repeat:
                reused=renderer.full(store.load(p['id']),lambda *_:None)
            repeat.assert_not_called()
            self.assertTrue(reused['reused'])
            self.assertEqual(reused['fullAssemblyPolicy'],'format-guarded-original-audio-v1')

    def test_completed_legacy_silent_checkpoint_is_reused_without_retimestamping(self):
        with tempfile.TemporaryDirectory() as root:
            store,p,renderer=fixture(root);target=story_path(store,p)
            visual=target.with_name(target.stem+'-visual.mp4');visual.write_bytes(b'completed silent checkpoint')
            with patch.object(renderer,'run',side_effect=self.encoded) as run:
                result=renderer.full(p,lambda *_:None)
            args=run.call_args.args[0]
            self.assertEqual(args[:2],['-i',str(visual)])
            self.assertNotIn('-bsf:v',args)
            self.assertEqual(visual.read_bytes(),b'completed silent checkpoint')
            self.assertEqual(result['fullAssemblyPolicy'],'legacy-two-pass-v1')

    def test_forecast_uses_only_local_headers_and_never_fetches_unknown_assets(self):
        with tempfile.TemporaryDirectory() as root:
            store,p,renderer=fixture(root)
            with patch.object(store,'asset',side_effect=AssertionError('Forecast must not fetch')):
                self.assertEqual(renderer.full_mux_forecast(p)['eligibility'],'verified')
                p['intro'].update(enabled=True,audioPath='missing-intro.wav')
                unknown=renderer.full_mux_forecast(p)
                self.assertEqual(unknown['eligibility'],'unknown')
                self.assertEqual(unknown['effectivePolicy'],'legacy-two-pass-v1')
                p['intro']['audioPath']='intro.opus'
                self.assertEqual(renderer.full_mux_forecast(p)['eligibility'],'unsupported')
                p['intro']['enabled']=False;p['settings']['video']['fps']=60
                self.assertEqual(renderer.full_mux_forecast(p)['eligibility'],'unsupported')

    def test_failed_fresh_mux_keeps_chapters_and_does_not_publish_complete_story(self):
        with tempfile.TemporaryDirectory() as root:
            store,p,renderer=fixture(root)
            original={f.name:f.read_bytes() for f in store.folder(p['id']).glob('chapter-*.mp4')}
            with patch.object(renderer,'run',side_effect=RuntimeError('cancelled')):
                with self.assertRaisesRegex(RuntimeError,'cancelled'):renderer.full(p,lambda *_:None)
            self.assertFalse(story_path(store,p).exists())
            self.assertFalse(list(store.folder(p['id']).glob('*-visual.mp4')))
            self.assertEqual(original,{f.name:f.read_bytes() for f in store.folder(p['id']).glob('chapter-*.mp4')})

    def test_unproven_pcm_rate_retains_original_two_pass_mux(self):
        with tempfile.TemporaryDirectory() as root:
            store,p,renderer=fixture(root,audio_rate=16000)
            with patch.object(renderer,'run',side_effect=self.encoded) as run:
                renderer.full(p,lambda *_:None)
            self.assertEqual(run.call_count,2)
            final_args=run.call_args.args[0]
            self.assertEqual(final_args[0],'-i')
            self.assertTrue(final_args[1].endswith('-visual.mp4'))
            self.assertNotIn('-bsf:v',final_args)


@unittest.skipUnless(os.environ.get('STUDIO_TEST_FFMPEG'),'Set STUDIO_TEST_FFMPEG for real media verification')
class FullMuxMediaTest(unittest.TestCase):
    def test_real_aac_chapters_keep_frames_pcm_and_packet_timestamps(self):
        ffmpeg=os.environ['STUDIO_TEST_FFMPEG']
        probe=os.environ.get('STUDIO_TEST_FFPROBE') or str(Path(ffmpeg).with_name('ffprobe.exe' if os.name=='nt' else 'ffprobe'))
        for offset,fps,audio_rate in ((0,30,24000),(.017,30,24000),(-.017,30,24000),(0,60,8000),(0,60,16000),(0,24,48000)):
            with self.subTest(chapterDurationOffset=offset,fps=fps,audioRate=audio_rate),tempfile.TemporaryDirectory() as root:
                store,p,renderer=fixture(root,ffmpeg,fps,audio_rate);folder=store.folder(p['id'])
                for ch in p['chapters']:ch['audio']['duration']+=offset
                p=store.save(p);durations=[ch['audio']['duration'] for ch in p['chapters']]
                files=[folder/f'chapter-{ch["number"]}.mp4' for ch in p['chapters']]
                silent=folder/'baseline-silent.mp4';old=folder/'baseline.mp4'
                renderer.concat(files,silent,lambda *_:None,folder/'baseline.log',durations,video_only=True)
                listing=renderer.concat_listing([folder/ch['audio']['path'] for ch in p['chapters']],folder/'baseline-audio.txt')
                renderer.run(['-i',str(silent),'-f','concat','-safe','0','-i',str(listing),'-map','0:v:0','-map','1:a:0',
                    '-c:v','copy','-c:a','aac','-b:a','128k','-af','apad','-t',str(sum(durations)),
                    '-movflags','+faststart',str(old)],lambda *_:None,folder/'baseline.log')
                renderer.calls=[]
                new=store.asset(p['id'],renderer.full(p,lambda *_:None)['path'])
                direct=fps==30 and audio_rate==24000
                self.assertEqual(len(renderer.calls),1 if direct else 2)
                def decoded(path,stream,codec):
                    return subprocess.check_output([ffmpeg,'-v','error','-threads','1','-i',str(path),'-map',stream,
                        *codec,'-f','hash','-hash','sha256','-'],timeout=30)
                self.assertEqual(decoded(old,'0:v:0',['-pix_fmt','rgb24']),decoded(new,'0:v:0',['-pix_fmt','rgb24']))
                self.assertEqual(decoded(old,'0:a:0',['-c:a','pcm_s16le']),decoded(new,'0:a:0',['-c:a','pcm_s16le']))
                def packets(path):
                    return json.loads(subprocess.check_output([probe,'-v','error','-show_data_hash','sha256',
                        '-show_entries','packet=stream_index,pts,dts,duration,flags,data_hash,side_data_list:stream=codec_name,time_base,start_pts,duration_ts',
                        '-of','json',str(path)],timeout=30))
                self.assertEqual(packets(old),packets(new))
                self.assertEqual(bool(list(folder.glob('story-*-visual.mp4'))),not direct)


if __name__=='__main__':unittest.main()
