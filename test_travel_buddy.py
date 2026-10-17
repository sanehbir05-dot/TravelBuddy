#!/usr/bin/env python3
"""End-to-end tests for Travel Buddy. Run with:  python -m unittest test_travel_buddy -v"""
import os
import re
import tempfile
import unittest
from datetime import date, timedelta

_db_file = os.path.join(tempfile.mkdtemp(), 'test.db')
os.environ['DATABASE_URL'] = os.environ.get('TEST_DATABASE_URL', f'sqlite:///{_db_file}')
os.environ.pop('MAIL_USERNAME', None)
os.environ['RATELIMIT_ENABLED'] = '0'

from app import app, db, User, Trip, CompanionRequest, Block, Report, places_match  # noqa: E402

COLLEGE = 'Thapar Institute of Engineering & Technology (TIET), Patiala'
OTHER_COLLEGE = 'Panjab University — Chandigarh, Chandigarh'
TOMORROW = (date.today() + timedelta(days=1)).isoformat()


class TravelBuddyTest(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        with app.app_context():
            db.drop_all()
            db.create_all()
        self.clients = {}

    # helpers ───────────────────────────
    def client(self, who):
        if who not in self.clients:
            self.clients[who] = app.test_client()
        return self.clients[who]

    def csrf(self, c):
        with c.session_transaction() as s:
            s.setdefault('_csrf', 'test-token')
            return s['_csrf']

    def post(self, who, url, data=None, **kw):
        c = self.client(who)
        data = dict(data or {}, csrf_token=self.csrf(c))
        return c.post(url, data=data, **kw)

    def signup(self, who, gender='female', college=COLLEGE, batch='2026', contact='@' + 'x'):
        email = f'{who}@thapar.edu'
        r = self.post(who, '/register', {'name': f'{who.title()} Kumar', 'email': email, 'password': 'password123',
                                         'gender': gender, 'college': college, 'batch': batch, 'contact_info': contact})
        self.assertEqual(r.status_code, 302)
        page = self.client(who).get('/verify-otp').get_data(as_text=True)
        code = re.search(r'letter-spacing: 6px;">(\d{6})<', page).group(1)
        r = self.post(who, '/verify-otp', {'otp': code})
        self.assertIn('/dashboard', r.headers['Location'])
        with app.app_context():
            return User.query.filter_by(email=email).first().id

    def trip(self, who, origin='Patiala', dest='Delhi', time='18:00', mode='bus', female_only=False, day=TOMORROW):
        data = {'origin': origin, 'destination': dest, 'date': day, 'time': time, 'transport_mode': mode}
        if female_only:
            data['female_only'] = 'on'
        r = self.post(who, '/trips/new', data)
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:500])
        return int(re.search(r'/trips/(\d+)/matches', r.headers['Location']).group(1))

    # tests ─────────────────────────────
    def test_pages_render(self):
        c = self.client('anon')
        for url in ['/', '/login', '/register']:
            self.assertEqual(c.get(url).status_code, 200, url)
        self.signup('priya')
        for url in ['/dashboard', '/trips/new', '/search', '/profile']:
            self.assertEqual(self.client('priya').get(url).status_code, 200, url)

    def test_rejects_non_college_email_and_bad_csrf(self):
        r = self.post('anon', '/register', {'name': 'X', 'email': 'x@other.edu', 'password': 'password123',
                                            'gender': 'male', 'college': COLLEGE, 'batch': '2026'})
        self.assertIn('@thapar.edu', r.get_data(as_text=True))
        r = self.client('anon').post('/register', data={'name': 'X'})
        self.assertEqual(r.status_code, 400)

    def test_wrong_otp(self):
        self.post('a', '/register', {'name': 'A B', 'email': 'a@thapar.edu', 'password': 'password123',
                                     'gender': 'male', 'college': COLLEGE, 'batch': '2026'})
        r = self.post('a', '/verify-otp', {'otp': '000000'})
        self.assertIn('Wrong code', r.get_data(as_text=True))

    def test_place_matching(self):
        self.assertTrue(places_match('Delhi', 'New Delhi Railway Station'))
        self.assertTrue(places_match('ISBT Delhi', 'delhi'))
        self.assertFalse(places_match('Delhi', 'Chandigarh'))

    def test_matching_by_destination_time_and_college(self):
        self.signup('priya')
        self.signup('rahul', gender='male')
        self.signup('late', gender='male')
        self.signup('outsider', gender='male')
        with app.app_context():
            User.query.filter_by(email='outsider@thapar.edu').first().college = OTHER_COLLEGE
            db.session.commit()
        mine = self.trip('priya', time='18:00')
        self.trip('rahul', origin='Patiala Bus Stand', dest='New Delhi', time='19:30')
        self.trip('late', time='23:30')                      # outside 3h window
        self.trip('outsider', time='18:00')                  # different college
        page = self.client('priya').get(f'/trips/{mine}/matches').get_data(as_text=True)
        self.assertIn('Rahul K.', page)
        self.assertNotIn('Late K.', page)
        self.assertNotIn('Outsider', page)
        page = self.client('priya').get(f'/trips/{mine}/matches?window=6').get_data(as_text=True)
        self.assertIn('Late K.', page)

    def test_women_only_trips_hidden_from_men(self):
        self.signup('priya')
        self.signup('ananya')
        self.signup('rahul', gender='male')
        t = self.trip('priya', female_only=True)
        self.assertNotIn('Priya K.', self.client('rahul').get('/dashboard').get_data(as_text=True))
        self.assertNotIn('Priya K.', self.client('rahul').get('/search').get_data(as_text=True))
        self.assertEqual(self.client('rahul').get(f'/trips/{t}/request').status_code, 404)
        self.assertIn('Priya K.', self.client('ananya').get('/search').get_data(as_text=True))
        # A women-only trip also only matches women
        self.trip('rahul')
        self.assertNotIn('Rahul K.', self.client('priya').get(f'/trips/{t}/matches').get_data(as_text=True))

    def test_request_accept_chat_and_contact_sharing(self):
        self.signup('priya', contact='@priya.travels')
        self.signup('ananya', contact='+91 99999')
        their = self.trip('priya')
        mine = self.trip('ananya', time='19:00')
        r = self.post('ananya', f'/trips/{their}/request', {'sender_trip_id': mine, 'note': 'Hi!', 'share_contact': 'on'})
        self.assertEqual(r.status_code, 302)
        # duplicate request is blocked
        r = self.client('ananya').get(f'/trips/{their}/request')
        self.assertEqual(r.status_code, 302)
        with app.app_context():
            req = CompanionRequest.query.one()
            rid = req.id
        # chat closed before acceptance
        self.assertIn('/dashboard', self.client('ananya').get(f'/requests/{rid}/chat').headers['Location'])
        # only the receiver can accept
        self.assertEqual(self.post('ananya', f'/requests/{rid}/accept').status_code, 403)
        self.post('priya', f'/requests/{rid}/accept')
        page = self.client('priya').get(f'/requests/{rid}/chat').get_data(as_text=True)
        self.assertIn('+91 99999', page)                       # sender chose to share
        page = self.client('ananya').get(f'/requests/{rid}/chat').get_data(as_text=True)
        self.assertNotIn('@priya.travels', page)              # receiver did not share yet
        self.post('priya', f'/requests/{rid}/share-contact', {'share': '1'})
        self.assertIn('@priya.travels', self.client('ananya').get(f'/requests/{rid}/chat').get_data(as_text=True))
        self.post('ananya', f'/requests/{rid}/chat', {'message': 'See you at gate 2'})
        self.assertIn('See you at gate 2', self.client('priya').get(f'/requests/{rid}/chat').get_data(as_text=True))
        # outsiders cannot read the chat
        self.signup('eve')
        self.assertIn('/dashboard', self.client('eve').get(f'/requests/{rid}/chat').headers['Location'])

    def test_block_hides_both_ways_and_closes_requests(self):
        self.signup('priya')
        self.signup('rahul', gender='male')
        t = self.trip('priya')
        self.post('rahul', f'/trips/{t}/request')
        with app.app_context():
            rahul_id = User.query.filter_by(email='rahul@thapar.edu').first().id
        self.post('priya', f'/users/{rahul_id}/block')
        with app.app_context():
            self.assertEqual(CompanionRequest.query.one().status, 'cancelled')
        self.assertNotIn(f'/trips/{t}/request', self.client('rahul').get('/search').get_data(as_text=True))
        self.assertEqual(self.client('rahul').get(f'/trips/{t}/request').status_code, 404)

    def test_reports_auto_hide_after_threshold(self):
        bad = self.signup('bad', gender='male')
        for name in ['r1', 'r2', 'r3']:
            self.signup(name)
            self.post(name, f'/users/{bad}/report', {'reason': 'Spam'})
        with app.app_context():
            self.assertEqual(Report.query.count(), 3)
            self.assertEqual(Block.query.count(), 0)
            self.assertTrue(db.session.get(User, bad).is_suspended)
        self.assertIn('suspended', self.client('bad').get('/dashboard', follow_redirects=True).get_data(as_text=True))

    def test_cancel_trip_cancels_plans(self):
        self.signup('priya')
        self.signup('ananya')
        t = self.trip('priya')
        self.post('ananya', f'/trips/{t}/request')
        with app.app_context():
            rid = CompanionRequest.query.one().id
        self.post('priya', f'/requests/{rid}/accept')
        self.post('priya', f'/trips/{t}/cancel')
        with app.app_context():
            self.assertTrue(db.session.get(Trip, t).is_cancelled)
            self.assertEqual(db.session.get(CompanionRequest, rid).status, 'cancelled')

    def test_past_dates_rejected(self):
        self.signup('priya')
        r = self.post('priya', '/trips/new', {'origin': 'A', 'destination': 'B', 'transport_mode': 'bus',
                                              'date': (date.today() - timedelta(days=1)).isoformat(), 'time': '10:00'})
        self.assertIn('cannot be in the past', r.get_data(as_text=True))

    def test_password_reset(self):
        self.signup('priya')
        c = self.client('anon')
        r = self.post('anon', '/forgot-password', {'email': 'priya@thapar.edu'})
        self.assertIn('/reset-password', r.headers['Location'])
        page = c.get('/reset-password').get_data(as_text=True)
        code = re.search(r'letter-spacing: 6px;">(\d{6})<', page).group(1)
        r = self.post('anon', '/reset-password', {'otp': '000000' if code != '000000' else '111111', 'password': 'newpassword1'})
        self.assertIn('Wrong code', r.get_data(as_text=True))
        r = self.post('anon', '/reset-password', {'otp': code, 'password': 'newpassword1'})
        self.assertIn('/login', r.headers['Location'])
        r = self.post('fresh', '/login', {'email': 'priya@thapar.edu', 'password': 'newpassword1'})
        self.assertIn('/dashboard', r.headers['Location'])
        # unknown emails get the same response and no code
        r = self.post('anon2', '/forgot-password', {'email': 'nobody@thapar.edu'})
        self.assertIn('/reset-password', r.headers['Location'])
        self.assertNotIn('Dev mode', self.client('anon2').get('/reset-password').get_data(as_text=True))

    def test_delete_account(self):
        uid = self.signup('priya')
        self.signup('ananya')
        t = self.trip('priya')
        self.post('ananya', f'/trips/{t}/request')
        r = self.post('priya', '/account/delete', {'password': 'wrong'})
        with app.app_context():
            self.assertFalse(db.session.get(User, uid).is_deleted)
        self.post('priya', '/account/delete', {'password': 'password123'})
        with app.app_context():
            u = db.session.get(User, uid)
            self.assertTrue(u.is_deleted)
            self.assertNotIn('priya', u.email)
            self.assertEqual(CompanionRequest.query.one().status, 'cancelled')
        self.assertNotIn(f'/trips/{t}/request', self.client('ananya').get('/search').get_data(as_text=True))
        r = self.post('again', '/login', {'email': 'priya@thapar.edu', 'password': 'password123'})
        self.assertIn('Wrong email or password', r.get_data(as_text=True))

    def test_chat_polling_endpoint(self):
        self.signup('priya')
        self.signup('ananya')
        t = self.trip('priya')
        self.post('ananya', f'/trips/{t}/request')
        with app.app_context():
            rid = CompanionRequest.query.one().id
        self.assertEqual(self.client('ananya').get(f'/requests/{rid}/messages.json').status_code, 403)
        self.post('priya', f'/requests/{rid}/accept')
        self.post('ananya', f'/requests/{rid}/chat', {'message': 'hello'})
        data = self.client('priya').get(f'/requests/{rid}/messages.json?after=0').get_json()
        self.assertEqual([m['text'] for m in data['messages']], ['hello'])
        self.assertFalse(data['messages'][0]['mine'])
        last = data['messages'][0]['id']
        self.assertEqual(self.client('priya').get(f'/requests/{rid}/messages.json?after={last}').get_json()['messages'], [])

    def test_app_shell_routes(self):
        c = self.client('anon')
        for url in ['/privacy', '/offline', '/sw.js', '/manifest.webmanifest', '/healthz', '/forgot-password']:
            self.assertEqual(c.get(url).status_code, 200, url)
        self.assertEqual(c.get('/nope').status_code, 404)

    def test_postgres_url_is_normalised(self):
        import app as app_module
        os.environ['DATABASE_URL'] = 'postgres://u:p@host/db'
        try:
            self.assertEqual(app_module.database_url(), 'postgresql+psycopg://u:p@host/db')
        finally:
            os.environ['DATABASE_URL'] = os.environ.get('TEST_DATABASE_URL', f'sqlite:///{_db_file}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
