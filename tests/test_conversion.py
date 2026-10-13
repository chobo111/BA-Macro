import unittest
from domain import Profile, Row
from conversion import plan_conversion


class ConversionTests(unittest.TestCase):
    def test_expanded_script_over_limit_blocks_conversion_without_mutating_source(self):
        profile = Profile([Row(action_mode='script', script='click 99,99\n' * 8000)], (100,100))
        before = profile.export()
        Profile.read(before)
        plan = plan_conversion(profile, (100,100), (1000,1000))
        self.assertEqual(len(plan.profile.rows[0].script), 112000)
        self.assertIn('변환 후 스크립트', '\n'.join(plan.errors))
        self.assertIn('100,000', '\n'.join(plan.errors))
        self.assertEqual(profile.export(), before)

    def test_converted_script_length_boundary_and_reload(self):
        from script_language import compile_script
        for extra in (0, 1):
            with self.subTest(extra=extra):
                source = ('click 99,99\n' * 7000) + '//' + ('x' * (1998 + extra))
                profile = Profile([Row(action_mode='script', script=source)], (100,100))
                compile_script(source)
                plan = plan_conversion(profile, (100,100), (1000,1000))
                self.assertEqual(len(plan.profile.rows[0].script), 100000 + extra)
                self.assertEqual(bool(plan.errors), bool(extra))
                if not extra:
                    self.assertEqual(Profile.read(plan.profile.export()), plan.profile)
                    compile_script(plan.profile.rows[0].script)

    def test_success_and_invalid_size_preserve_original_profile(self):
        profile = Profile([Row(x=100, y=200)], (1000,1000))
        before = profile.export()
        plan = plan_conversion(profile, (1000,1000), (2000,2000))
        self.assertFalse(plan.errors)
        self.assertEqual((plan.profile.rows[0].x, plan.profile.rows[0].y), (200,400))
        self.assertEqual(plan.profile.resolution, (2000,2000))
        self.assertEqual(profile.export(), before)
        with self.assertRaises(ValueError):
            plan_conversion(profile, (0,1), (1,1))
        self.assertEqual(profile.export(), before)

    def test_script_preserves_syntax_comments_and_non_coordinate_values(self):
        source = ('loop 2\r\n  curve_first_time 5\r\ncurve_last_time 5\r\n'
                  '  CuRvE 100 , 200 mouse 300,400 // keep 100,200\r\n'
                  'click 100,200 300,400\r\nmove 100,200\r\npress 100,200\r\n'
                  'release 100,200\r\nswitch 100,200\r\nsleep 5\r\nloop_end\r\n'
                  '// keep "100,200" // comment\r\nrelease_actions\r\nclick 100,200\r\n')
        profile = Profile([Row(x=100,y=200,action_mode='script',script=source)], (1000,1000))
        original = profile.export()
        plan = plan_conversion(profile, (1000,1000), (500,500))
        self.assertFalse(plan.errors)
        script = plan.profile.rows[0].script
        self.assertIn('  CuRvE 50 , 100 mouse 150,200 // keep 100,200\r\n', script)
        self.assertIn('// keep "100,200" // comment\r\n', script)
        self.assertIn('sleep 5\r\nloop_end', script)
        self.assertIn('release_actions\r\nclick 50,100', script)
        self.assertEqual(plan.script_points, 9)
        self.assertEqual(profile.export(), original)

    def test_errors_do_not_partially_mutate_profile(self):
        profile = Profile([Row(x=10,y=20),Row(action_mode='script',script='click 1000,20')],(1000,1000))
        original = profile.export()
        plan = plan_conversion(profile, (1000,1000), (500,500))
        self.assertTrue(plan.errors)
        self.assertIn('범위 밖', '\n'.join(plan.errors))
        self.assertEqual(original, profile.export())
        profile.rows[1].script = 'curve X,Y mouse'
        self.assertTrue(plan_conversion(profile,(1000,1000),(500,500)).errors)

    def test_edge_rounding_preserves_aspect(self):
        plan = plan_conversion(Profile([Row(x=999,y=999)]),(1000,1000),(3,3))
        self.assertEqual((plan.profile.rows[0].x,plan.profile.rows[0].y),(2,2))
        self.assertFalse(plan.warnings)
        self.assertFalse(plan.errors)

    def test_dormant_scripts_are_converted_and_mouse_unchanged(self):
        plan = plan_conversion(Profile([Row(script='click mouse 50,50')]),(100,100),(200,200))
        self.assertEqual(plan.profile.rows[0].script,'click mouse 100,100')
        self.assertEqual(plan.script_points,1)

    def test_negative_and_invalid_resolution_rejected(self):
        profile = Profile([Row(x=-1,y=0)])
        self.assertTrue(plan_conversion(profile,(100,100),(200,200)).errors)
        with self.assertRaises(ValueError):
            plan_conversion(profile,(0,100),(200,200))

    def test_empty_scripts_preserved_while_other_coordinates_convert(self):
        for blank in ('', '  \t\n'):
            with self.subTest(blank=blank):
                profile = Profile([
                    Row(x=100, y=200, action_mode='script', script=blank),
                    Row(action_mode='script', script='curve 300,400 mouse')], (1000,1000))
                original = profile.export()
                plan = plan_conversion(profile, (1000,1000), (500,500))
                self.assertEqual(plan.errors, [])
                self.assertEqual(plan.profile.rows[0].script, blank)
                self.assertEqual((plan.profile.rows[0].x, plan.profile.rows[0].y), (50,100))
                self.assertEqual(plan.profile.rows[1].script, 'curve 150,200 mouse')
                self.assertEqual(plan.script_points, 1)
                self.assertEqual(profile.export(), original)

    def test_empty_script_skip_does_not_hide_errors_or_allow_empty_execution(self):
        from script_language import compile_script
        with self.assertRaises(ValueError):
            compile_script('')
        for source in ('curve X,Y mouse', 'click 1000,10', 'key_press a'):
            with self.subTest(source=source):
                profile = Profile([Row(action_mode='script', script=''),
                                   Row(action_mode='script', script=source)])
                self.assertTrue(plan_conversion(profile, (1000,1000), (500,500)).errors)

    def test_different_aspect_warns_without_mutating_original(self):
        profile = Profile([Row(x=100,y=200,script='curve 10,20 mouse')], (1920,1080))
        before = profile.export()
        plan = plan_conversion(profile, profile.resolution, (1280,960))
        self.assertTrue(plan.warnings)
        self.assertFalse(plan.errors)
        self.assertEqual(plan.profile.resolution, (1280,960))
        self.assertEqual(profile.export(), before)

    def test_aggregate_counts_unchanged_rounded_and_nested_release_points(self):
        profile = Profile([Row(x=0, y=0, script=(
            'loop 2\ncurve 1,1 mouse 2,2\nloop_end\nrelease_actions\nclick 0,0 10,10'))], (100,100))
        plan = plan_conversion(profile, (100,100), (50,50))
        self.assertEqual(plan.changed_points, 3)
        self.assertEqual(plan.script_points, 4)
        self.assertEqual(plan.errors, [])

    def test_cancelled_conversion_preserves_original(self):
        from concurrent.futures import CancelledError
        profile = Profile([Row(x=1, y=2, script='click 10,10\n' * 100)])
        before = profile.export()
        checks = []
        def cancelled():
            checks.append(True)
            return len(checks) > 10
        with self.assertRaises(CancelledError):
            plan_conversion(profile, (100,100), (200,200), cancelled=cancelled)
        self.assertEqual(before, profile.export())

    def test_error_detail_limit_preserves_total_and_original(self):
        for count in (0, 1, 3, 4, 200):
            with self.subTest(count=count):
                profile = Profile([Row(x=-1) for _ in range(count)] or [Row()], (100,100))
                original = profile.export()
                plan = plan_conversion(profile, (100,100), (200,200))
                self.assertEqual(plan.error_count, count)
                self.assertEqual(len(plan.errors), min(count, 3))
                self.assertEqual(plan.errors, [
                    f'{i}행 · 표: -1, 0 — 원본 해상도 범위 밖입니다.'
                    for i in range(1, min(count, 3) + 1)])
                self.assertEqual(profile.export(), original)

    def test_error_total_includes_later_script_and_expansion_failures(self):
        profile = Profile([
            Row(x=-1, script='click -1,0 100,0'),
            Row(script='invalid'),
            Row(script='click 99,99\n' * 8000),
            Row(x=-1),
        ], (100,100))
        original = profile.export()
        plan = plan_conversion(profile, (100,100), (1000,1000))
        self.assertEqual(plan.error_count, 6)
        self.assertEqual(len(plan.errors), 3)
        self.assertTrue(all(message.startswith('1행') for message in plan.errors))
        self.assertEqual(plan.script_points, 8002)
        self.assertGreater(len(plan.profile.rows[2].script), 100000)
        self.assertEqual(profile.export(), original)
