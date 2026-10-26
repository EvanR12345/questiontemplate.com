"""Real codec regression tests: no model downloads or cloud calls."""
import math
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from studio_audio import OpusWriter, probe, transcode
from studio_engagement import ding


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required')
class OpusAssetsTest(unittest.TestCase):
    def signal(self, count):
        return b''.join(struct.pack('<h',round(5000*math.sin(i*.07))) for i in range(count))

    def test_exact_duration_and_decode_with_irregular_pcm_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            for count in (1,479,24001,72017):
                target=Path(directory)/f'{count}.opus'
                pcm=self.signal(count)
                with OpusWriter(target) as output:
                    output.setparams((1,2,24000,0,'NONE',''))
                    for start in range(0,len(pcm),2002):output.writeframes(pcm[start:start+2002])
                info=probe(target)
                self.assertEqual(info['codec'],'opus')
                self.assertAlmostEqual(info['duration'],count/24000,places=6)
                decoded=subprocess.run(['ffmpeg','-v','error','-i',str(target),'-f','f32le',
                    '-ar','48000','-ac','1','pipe:1'],capture_output=True,check=True)
                self.assertEqual(len(decoded.stdout)//8,count)
            self.assertFalse(list(Path(directory).glob('*.wav')))

    def test_cancelled_encoding_removes_partial_output_and_preserves_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'saved.opus';target.write_bytes(b'previous')
            partial=target.with_suffix('.partial.opus')
            with self.assertRaisesRegex(RuntimeError,'cancelled'):
                with OpusWriter(partial) as output:
                    output.setparams((1,2,24000,0,'NONE',''))
                    output.writeframes(self.signal(2400))
                    raise RuntimeError('cancelled')
            self.assertEqual(target.read_bytes(),b'previous')
            self.assertFalse(partial.exists())

    def test_empty_and_truncated_files_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'test.opus'
            with self.assertRaises(RuntimeError):
                with OpusWriter(target) as output:output.setparams((1,2,24000,0,'NONE',''))
            self.assertFalse(target.exists())
            ding(target)
            self.assertAlmostEqual(probe(target)['duration'],.6,places=6)
            target.write_bytes(target.read_bytes()[:-3])
            with self.assertRaises(ValueError):probe(target)

    def test_legacy_conversion_is_non_destructive_and_opus_master_is_reused(self):
        import wave
        from studio_data import ProjectStore, new_project
        from studio_render import VideoRenderer
        with tempfile.TemporaryDirectory() as directory:
            store=ProjectStore(directory);project=store.save(new_project())
            source=store.asset(project['id'],'legacy.wav')
            with wave.open(str(source),'wb') as output:
                output.setparams((1,2,24000,0,'NONE',''));output.writeframes(self.signal(24000))
            original=source.read_bytes();target=source.with_suffix('.opus')
            transcode(source,target)
            self.assertEqual(source.read_bytes(),original)
            self.assertAlmostEqual(probe(target)['duration'],1,places=6)
            renderer=VideoRenderer(store,{})
            with patch.object(renderer,'run',side_effect=AssertionError('Unexpected encoding')):
                self.assertEqual(renderer.audio_preview(project['id'],target.name,lambda *_:None)['exportPath'],target.name)

    def test_old_audition_converts_once_without_synthesis_or_chapter_changes(self):
        import copy
        from unittest.mock import Mock
        from studio_service import StudioService
        from studio_data import ProjectStore, new_project
        from studio_render import VideoRenderer
        with tempfile.TemporaryDirectory() as directory:
            service=StudioService.__new__(StudioService)
            service.store=ProjectStore(directory);service.gate=lambda *_:None
            service.renderer=VideoRenderer(service.store,{'ffmpeg':shutil.which('ffmpeg')})
            service.create_selected_audio=Mock(side_effect=AssertionError('Unwanted synthesis'))
            project=new_project();legacy='voice-preview-'+('a'*16)+'.wav'
            project['voicePreviews']=[{'path':legacy,'duration':.6,'text':'The gate opened.'}]
            project=service.store.save(project);chapters=copy.deepcopy(project['chapters'])
            import wave
            with wave.open(str(service.store.asset(project['id'],legacy)),'wb') as output:
                output.setparams((1,2,24000,0,'NONE',''));output.writeframes(self.signal(14400))
            with patch('studio_service.digest',return_value='a'*32),patch('studio_audio.transcode',wraps=transcode) as convert:
                service.voice_preview(project,{'text':'The gate opened.'})
                service.voice_preview(service.store.load(project['id']),{'text':'The gate opened.'})
                self.assertEqual(convert.call_count,1)
            saved=service.store.load(project['id'])
            self.assertEqual(saved['chapters'],chapters)
            self.assertTrue(service.store.asset(project['id'],legacy).is_file())
            self.assertEqual(saved['voicePreviews'][-1]['audioCodec'],'opus')
            self.assertEqual(len(list(Path(directory).rglob('*.opus'))),1)

    def test_real_chapter_render_reads_opus_without_a_wav_intermediary(self):
        from PIL import Image
        from studio_data import ProjectStore, new_project
        from studio_render import VideoRenderer
        with tempfile.TemporaryDirectory() as directory:
            store=ProjectStore(directory);project=store.save(new_project())
            project['settings']['video'].update(width=640,height=360,fps=30)
            project['settings']['qcCheckLevel']='off'
            root=store.folder(project['id']);ding(root/'narration.opus')
            Image.new('RGB',(640,360),'navy').save(root/'shot.png')
            chapter=project['chapters'][0]
            chapter['audio']={'path':'narration.opus','duration':.6}
            chapter['scenes']=[{'shots':[{'id':'one','start':0,'end':.6,
                'imagePath':'shot.png','motion':'static'}]}]
            renderer=VideoRenderer(store,{'ffmpeg':shutil.which('ffmpeg'),'renderWorkers':1})
            result=renderer.chapter(project,chapter,lambda *_:None)
            video=store.asset(project['id'],result['path'])
            self.assertTrue(video.is_file())
            subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(video),'-f','null','-'],
                capture_output=True,check=True)
            self.assertFalse(list(root.rglob('*.wav')))


if __name__=='__main__':unittest.main()
