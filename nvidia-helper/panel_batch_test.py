import unittest
from PIL import Image
from panel_batch import panel_prompt, split_panels, validate_groups

class PanelBatchTest(unittest.TestCase):
    def test_each_panel_has_correct_landscape_crop_and_retained_box(self):
        image = Image.new('RGB',(1344,768))
        colors = ['red','green','blue','yellow']
        for index,color in enumerate(colors):
            image.paste(color,((index%2)*672,(index//2)*384,(index%2+1)*672,(index//2+1)*384))
        results = split_panels(image,4)
        for index,result in enumerate(results):
            self.assertEqual(result['pil'].size,(640,360))
            self.assertEqual(result['panelIndex'],index)
            self.assertEqual(result['pil'].getpixel((320,180)),Image.new('RGB',(1,1),colors[index]).getpixel((0,0)))
    def test_group_coverage_never_omits_or_duplicates_a_shot(self):
        shots = [{'id':str(i)} for i in range(6)]
        self.assertEqual(validate_groups([['0','1','2','3'],['4','5']],shots)[1],['4','5'])
        for groups in ([['0']], [['0','1','2','3','4','5']], [['0','1','2','3'],['3','4','5']]):
            with self.assertRaises(ValueError):
                validate_groups(groups,shots)
        prompt = panel_prompt([{'prompt':'Mira closes a door.'},{'prompt':'The room is empty.'}])
        self.assertIn('TOP LEFT QUADRANT ONLY: Mira closes a door.',prompt)
        self.assertIn('TOP RIGHT QUADRANT ONLY: The room is empty.',prompt)

if __name__ == '__main__':
    unittest.main()
