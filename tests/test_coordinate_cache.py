import unittest
from coordinate_cache import scaled_point, scaled_program
from script_language import compile_script

class Coordinates(unittest.TestCase):
    def test_point_and_bounds(self):
        self.assertEqual(scaled_point((640,360),(1280,720),(1920,1080)), (960,540))
        self.assertEqual(scaled_point((1279,719),(1280,720),(320,180)), (319,179))
        with self.assertRaises(ValueError):
            scaled_point((1280,0),(1280,720),(1920,1080))

    def test_nested_and_release_commands_preserve_mouse_and_source(self):
        script = 'loop 2\ncurve 640,360 mouse\nsleep 5\nloop_end\nrelease_actions\nclick 100,200'
        original = compile_script(script)
        result = scaled_program(script,(1280,720),(1920,1080))
        self.assertEqual(result.pressed[0].body[0].args, ((960,540),'mouse'))
        self.assertEqual(result.pressed[0].body[1].args, (5,))
        self.assertEqual(result.released[0].args, ((150,300),))
        self.assertEqual(original.pressed[0].body[0].args, ((640,360),'mouse'))
        self.assertIs(result, scaled_program(script,(1280,720),(1920,1080)))

    def test_changes_create_new_cache_and_do_not_accumulate_rounding(self):
        self.assertEqual(scaled_program('click 1,1',(3,3),(2,2)).pressed[0].args, ((1,1),))
        self.assertEqual(scaled_program('click 1,1',(3,3),(6,6)).pressed[0].args, ((2,2),))
        self.assertEqual(scaled_program('click 2,2',(3,3),(6,6)).pressed[0].args, ((4,4),))
