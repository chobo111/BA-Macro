import unittest
from domain import Profile, Row
from conversion import plan_conversion


class ConversionTests(unittest.TestCase):
    def test_script_preserves_syntax_comments_and_non_coordinate_values(self):
        source = ('loop 2\r\n  curve_first_time 5\r\ncurve_last_time 5\r\n'
                  '  CuRvE 100 , 200 mouse 300,400 // keep 100,200\r\n'
                  'click 100,200 300,400\r\nmove 100,200\r\npress 100,200\r\n'
                  'release 100,200\r\nswitch 100,200\r\nsleep 5\r\nloop_end\r\n'
                  'text "100,200 // text"\r\nrelease_actions\r\nclick 100,200\r\n')
        profile = Profile([Row(x=100,y=200,action_mode='script',script=source)], (1000,1000))
        original = profile.export()
        plan = plan_conversion(profile, (1000,1000), (500,500))
        self.assertFalse(plan.errors)
        script = plan.profile.rows[0].script
        self.assertIn('  CuRvE 50 , 100 mouse 150,200 // keep 100,200\r\n', script)
        self.assertIn('text "100,200 // text"\r\n', script)
        self.assertIn('sleep 5\r\nloop_end', script)
        self.assertIn('release_actions\r\nclick 50,100', script)
        self.assertEqual(plan.script_points, 9)
        self.assertEqual(profile.export(), original)

    def test_errors_do_not_partially_mutate_profile(self):
        profile = Profile([Row(x=10,y=20),Row(action_mode='script',script='click 1000,20')],(1000,1000))
        original = profile.export()
        with self.assertRaisesRegex(ValueError, '범위 밖'):
            profile.convert((1000,1000),(500,500))
        self.assertEqual(original, profile.export())
        profile.rows[1].script = 'curve X,Y mouse'
        self.assertTrue(plan_conversion(profile,(1000,1000),(500,500)).errors)

    def test_edge_rounding_and_aspect_warning(self):
        plan = plan_conversion(Profile([Row(x=999,y=999)]),(1000,1000),(3,2))
        self.assertEqual((plan.profile.rows[0].x,plan.profile.rows[0].y),(2,1))
        self.assertTrue(plan.warnings)
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
