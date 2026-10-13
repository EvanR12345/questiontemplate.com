import copy,unittest
from studio_timing_units import visual_timing_units,rebase_source_indices

class TimingUnitsTest(unittest.TestCase):
 def test_long_sentence_can_support_selected_cadence_without_changing_story_or_audio_bounds(self):
  original=[{'index':0,'start':0,'end':40,'text':'Christopher walked into the room, and Jonathan quietly watched him before the door closed.'}]
  before=copy.deepcopy(original);units=visual_timing_units(original,8)
  self.assertGreaterEqual(len(units),6);self.assertEqual(' '.join(s['text'] for s in units),original[0]['text'])
  self.assertEqual(units[0]['start'],0);self.assertEqual(units[-1]['end'],40)
  self.assertTrue(all(a['end']==b['start'] for a,b in zip(units,units[1:])))
  self.assertTrue(all('Estimated' in s['timingSource'] for s in units));self.assertEqual(original,before)
 def test_many_short_source_sentences_are_not_expanded(self):
  original=[{'index':i,'start':i,'end':i+1,'text':'A quiet moment.'} for i in range(20)]
  self.assertEqual(visual_timing_units(original,6),original)
 def test_one_word_never_becomes_invented_extra_events(self):
  original=[{'index':0,'start':0,'end':30,'text':'Boom!'}]
  self.assertEqual(len(visual_timing_units(original,8)),1)
 def test_fact_indices_map_to_original_sentence_once_and_keep_exact_evidence(self):
  original=[{'index':0,'start':0,'end':30,'text':'Mira walks slowly down the hall, while the teacher waits at the door.'},
            {'index':1,'start':30,'end':60,'text':'Mira takes the silver key, and puts it in her right hand.'}]
  facts={'changes':[{'sentence':1,'reason':original[1]['text'],'characterId':'mira','field':'key','value':'right hand'}],
         'dialogueSpeakers':[{'sentence':1,'cueSentence':0,'characterId':'mira'}]}
  before=copy.deepcopy(facts);units=visual_timing_units(original,8);mapped=rebase_source_indices(facts,units)
  first=next(i for i,s in enumerate(units) if s['originalSentenceIndex']==1)
  self.assertEqual(mapped['changes'][0]['sentence'],first)
  self.assertEqual(mapped['dialogueSpeakers'][0]['sentence'],first)
  self.assertEqual(mapped['dialogueSpeakers'][0]['cueSentence'],0)
  self.assertEqual(mapped['changes'][0]['reason'],original[1]['text']);self.assertEqual(facts,before)
  self.assertEqual(len(mapped['changes']),1)

if __name__=='__main__':unittest.main()
