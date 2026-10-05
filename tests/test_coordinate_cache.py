import unittest
from coordinate_cache import scaled_point, scaled_program
from conversion import plan_conversion
from domain import Profile, Row
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

    def test_file_and_runtime_conversion_match_all_coordinate_commands(self):
        script = (
            'loop 2\r\n'
            '  loop 3\r\n'
            '    CuRvE 999 , 599 mouse 1,1 // keep 999,599\r\n'
            '    click mouse 500,300\r\n'
            '  loop_end\r\n'
            '  move 100,200\r\n'
            '  press 200,300\r\n'
            '  release 300,400\r\n'
            '  switch 400,500\r\n'
            '  sleep 17\r\n'
            'loop_end\r\n'
            'release_actions\r\n'
            'loop 2\r\n'
            '  curve mouse 999,599\r\n'
            '  click 0,0 mouse\r\n'
            'loop_end\r\n')
        source, destination = (1000, 600), (3, 2)
        profile = Profile([Row(x=999, y=599, action_mode='script', script=script)], source)
        original = profile.export()
        plan = plan_conversion(profile, source, destination)
        self.assertEqual(plan.errors, [])
        converted = plan.profile.rows[0]
        self.assertEqual((converted.x, converted.y), scaled_point((999, 599), source, destination))
        self.assertEqual(compile_script(converted.script), scaled_program(script, source, destination))
        self.assertIn('    CuRvE 2 , 1 mouse 0,0 // keep 999,599\r\n', converted.script)
        self.assertEqual(profile.export(), original)

    def test_file_and_runtime_conversion_match_rounding_and_edges(self):
        cases = (
            ((0, 0), (1, 1), (100000, 100000), (0, 0)),
            ((99999, 99999), (100000, 100000), (1, 1), (0, 0)),
            ((1, 3), (4, 4), (2, 2), (0, 1)),
            ((3, 1), (4, 4), (10, 10), (8, 2)),
        )
        for point, source, destination, expected in cases:
            with self.subTest(point=point, source=source, destination=destination):
                plan = plan_conversion(Profile([Row(x=point[0], y=point[1])]), source, destination)
                self.assertEqual(plan.errors, [])
                row = plan.profile.rows[0]
                self.assertEqual((row.x, row.y), expected)
                self.assertEqual(scaled_point(point, source, destination), expected)

    def test_invalid_points_keep_file_locations_and_runtime_error(self):
        for point in ((-1, 0), (0, -1), (100, 0), (0, 60)):
            with self.subTest(point=point):
                x, y = point
                profile = Profile([Row(move_hk='q', x=x, y=y, script=f'click {x},{y}')])
                plan = plan_conversion(profile, (100, 60), (200, 120))
                self.assertEqual(plan.errors, [
                    f'1행 · Q · 표: {x}, {y} — 원본 해상도 범위 밖입니다.',
                    f'1행 · Q · 스크립트 1줄: {x}, {y} — 원본 해상도 범위 밖입니다.',
                ])
                with self.assertRaisesRegex(ValueError, '^원본 좌표가 프로필 기준 해상도 밖에 있습니다.$'):
                    scaled_point(point, (100, 60), (200, 120))
                with self.assertRaisesRegex(ValueError, '^원본 좌표가 프로필 기준 해상도 밖에 있습니다.$'):
                    scaled_program(profile.rows[0].script, (100, 60), (200, 120))
