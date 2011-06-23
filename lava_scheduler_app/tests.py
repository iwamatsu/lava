import datetime
import json
import xmlrpclib

from django.contrib.auth.models import Permission, User
from django.test import TestCase

from lava_scheduler_app.models import Device, DeviceType, TestJob

import cStringIO

from xmlrpclib import ServerProxy, Transport

from django.test.client import Client

# Based on http://www.technobabble.dk/2008/apr/02/xml-rpc-dispatching-through-django-test-client/
class TestTransport(Transport):
    """Handles connections to XML-RPC server through Django test client."""

    def __init__(self, user=None, password=None):
        self.client = Client()
        if user:
            success = self.client.login(username=user, password=password)
            if not success:
                raise AssertionError("Login attempt failed!")
        self._use_datetime = True

    def request(self, host, handler, request_body, verbose=0):
        self.verbose = verbose
        response = self.client.post(
            handler, request_body, content_type="text/xml")
        res = cStringIO.StringIO(response.content)
        res.seek(0)
        return self.parse_response(res)


class ModelFactory(object):

    def __init__(self):
        self._int = 0

    def getUniqueInteger(self):
        self._int += 1
        return self._int

    def getUniqueString(self, prefix='generic'):
        return '%s-%d' % (prefix, self.getUniqueInteger())

    def make_user(self):
        return User.objects.create_user(
            self.getUniqueString(),
            '%s@mail.invalid' % (self.getUniqueString(),),
            self.getUniqueString())

    def ensure_device_type(self, name=None):
        if name is None:
            name = self.getUniqueString('name')
        return DeviceType.objects.get_or_create(name=name)[0]

    def make_device(self, device_type=None, hostname=None):
        if device_type is None:
            device_type = self.ensure_device_type()
        if hostname is None:
            hostname = self.getUniqueString()
        device = Device(device_type=device_type, hostname=hostname)
        device.save()
        return device

    def make_testjob(self, target=None, device_type=None, definition=None):
        if device_type is None:
            device_type = self.ensure_device_type()
        if definition is None:
            definition = json.dumps({})
        submitter = self.make_user()
        testjob = TestJob(
            device_type=device_type, target=target, definition=definition,
            submitter=submitter)
        testjob.save()
        return testjob


class TestCaseWithFactory(TestCase):

    def setUp(self):
        TestCase.setUp(self)
        self.factory = ModelFactory()


class TestTestJob(TestCaseWithFactory):

    def test_from_json_and_user_sets_definition(self):
        self.factory.ensure_device_type(name='panda')
        definition = json.dumps({'device_type':'panda'})
        job = TestJob.from_json_and_user(definition, self.factory.make_user())
        self.assertEqual(definition, job.definition)

    def test_from_json_and_user_sets_submitter(self):
        self.factory.ensure_device_type(name='panda')
        user = self.factory.make_user()
        job = TestJob.from_json_and_user(
            json.dumps({'device_type':'panda'}), user)
        self.assertEqual(user, job.submitter)

    def test_from_json_and_user_sets_device_type(self):
        panda_type = self.factory.ensure_device_type(name='panda')
        job = TestJob.from_json_and_user(
            json.dumps({'device_type':'panda'}), self.factory.make_user())
        self.assertEqual(panda_type, job.device_type)

    def test_from_json_and_user_sets_target(self):
        panda_board = self.factory.make_device(hostname='panda01')
        job = TestJob.from_json_and_user(
            json.dumps({'target':'panda01'}), self.factory.make_user())
        self.assertEqual(panda_board, job.target)

    def test_from_json_and_user_sets_device_type_from_target(self):
        panda_type = self.factory.ensure_device_type(name='panda')
        self.factory.make_device(device_type=panda_type, hostname='panda01')
        job = TestJob.from_json_and_user(
            json.dumps({'target':'panda01'}), self.factory.make_user())
        self.assertEqual(panda_type, job.device_type)

    def test_from_json_and_user_sets_date_submitted(self):
        self.factory.ensure_device_type(name='panda')
        before = datetime.datetime.now()
        job = TestJob.from_json_and_user(
            json.dumps({'device_type':'panda'}), self.factory.make_user())
        after = datetime.datetime.now()
        self.assertTrue(before < job.submit_time < after)

    def test_from_json_and_user_sets_status_to_SUBMITTED(self):
        self.factory.ensure_device_type(name='panda')
        job = TestJob.from_json_and_user(
            json.dumps({'device_type':'panda'}), self.factory.make_user())
        self.assertEqual(job.status, TestJob.SUBMITTED)


class TestSchedulerAPI(TestCaseWithFactory):

    def server_proxy(self, user=None, password=None):
        return ServerProxy(
            'http://localhost/RPC2/',
            transport=TestTransport(user=user, password=password))

    def test_api_rejects_anonymous(self):
        server = self.server_proxy()
        try:
            server.scheduler.submit_job("{}")
        except xmlrpclib.Fault as f:
            self.assertEqual(401, f.faultCode)
        else:
            self.fail("fault not raised")

    def test_api_rejects_unpriv_user(self):
        User.objects.create_user('test', 'e@mail.invalid', 'test').save()
        server = self.server_proxy('test', 'test')
        try:
            server.scheduler.submit_job("{}")
        except xmlrpclib.Fault as f:
            self.assertEqual(403, f.faultCode)
        else:
            self.fail("fault not raised")

    def test_sets_definition(self):
        user = User.objects.create_user('test', 'e@mail.invalid', 'test')
        user.user_permissions.add(
            Permission.objects.get(codename='add_testjob'))
        user.save()
        server = self.server_proxy('test', 'test')
        self.factory.ensure_device_type(name='panda')
        definition = json.dumps({'device_type':'panda'})
        job_id = server.scheduler.submit_job(definition)
        job = TestJob.objects.get(id=job_id)
        self.assertEqual(definition, job.definition)


from django.test import TransactionTestCase

from lava_scheduler_daemon.dbjobsource import DatabaseJobSource

class TransactionTestCaseWithFactory(TransactionTestCase):

    def setUp(self):
        TransactionTestCase.setUp(self)
        self.factory = ModelFactory()


class TestDBJobSource(TransactionTestCaseWithFactory):

    def test_getBoardList(self):
        self.factory.make_device(hostname='panda01')
        self.assertEqual(['panda01'], DatabaseJobSource().getBoardList_impl())

    def test_getJobForBoard_returns_json(self):
        device = self.factory.make_device(hostname='panda01')
        definition = {'foo': 'bar'}
        self.factory.make_testjob(
            target=device, definition=json.dumps(definition))
        self.assertEqual(
            definition, DatabaseJobSource().getJobForBoard_impl('panda01'))

    def test_getJobForBoard_sets_start_time(self):
        device = self.factory.make_device(hostname='panda01')
        job = self.factory.make_testjob(target=device)
        before = datetime.datetime.now()
        DatabaseJobSource().getJobForBoard_impl('panda01')
        after = datetime.datetime.now()
        # reload from the database
        job = TestJob.objects.get(pk=job.pk)
        self.assertTrue(before < job.start_time < after)
