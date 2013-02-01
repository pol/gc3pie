#! /usr/bin/env python
#
"""
Unit tests for the `gc3libs.backends.shellcmd` module.
"""
# Copyright (C) 2011-2012 GC3, University of Zurich. All rights reserved.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
__docformat__ = 'reStructuredText'
__version__ = '$Revision$'


import errno
import os
import shutil
import sys
import tempfile
import time

from nose.tools import raises, assert_equal

import gc3libs
from gc3libs.authentication import Auth
import gc3libs.config
import gc3libs.core
from gc3libs.quantity import Memory


class TestBackendShellcmd(object):
    CONF = """
[resource/localhost_test]
type=shellcmd
transport=local
time_cmd=/usr/bin/time
max_cores=4
max_cores_per_job=4
max_memory_per_core=2
max_walltime=2
architecture=x64_64
auth=noauth
enabled=True

[auth/noauth]
type=none
"""

    def setUp(self):
        (fd, cfgfile) = tempfile.mkstemp()
        f = os.fdopen(fd, 'w+')
        f.write(TestBackendShellcmd.CONF)
        f.close()
        self.files_to_remove = [cfgfile]
        self.apps_to_kill = []

        self.cfg = gc3libs.config.Configuration()
        self.cfg.merge_file(cfgfile)

        self.core = gc3libs.core.Core(self.cfg)
        self.backend = self.core.get_backend('localhost_test')
        # Update resource status
        self.backend.get_resource_status()

    def cleanup_file(self, fname):
        self.files_to_remove.append(fname)

    def tearDown(self):
        for fname in self.files_to_remove:
            if os.path.isdir(fname):
                shutil.rmtree(fname)
            elif os.path.exists(fname):
                os.remove(fname)

        for app in self.apps_to_kill:
            try:
                self.core.kill(app)
            except:
                pass
            try:
                self.core.free(app)
            except:
                pass

    def test_submission_ok(self):
        """
        Test a successful submission cycle and the backends' resource
        book-keeping.
        """
        tmpdir = tempfile.mkdtemp(prefix=__name__, suffix='.d')
        ncores = self.backend.max_cores

        app = gc3libs.Application(
            arguments=['/usr/bin/env'],
            inputs=[],
            outputs=[],
            output_dir=tmpdir,
            stdout="stdout.txt",
            stderr="stderr.txt",
            requested_cores=1, )
        self.core.submit(app)
        self.apps_to_kill.append(app)

        self.cleanup_file(tmpdir)
        self.cleanup_file(app.execution.lrms_execdir)

        # there's no SUBMITTED state here: jobs go immediately into
        # RUNNING state
        assert_equal(app.execution.state, gc3libs.Run.State.SUBMITTED)
        assert_equal(self.backend.free_slots,  ncores - 1)
        assert_equal(self.backend.user_queued, 0)
        assert_equal(self.backend.user_run,    1)

        # wait until the test job is done, but timeout and raise an error
        # if it takes too much time...
        MAX_WAIT = 10  # seconds
        WAIT = 0.1  # seconds
        waited = 0
        while app.execution.state != gc3libs.Run.State.TERMINATING \
                and waited < MAX_WAIT:
            time.sleep(WAIT)
            waited += WAIT
            self.core.update_job_state(app)
        assert_equal(app.execution.state, gc3libs.Run.State.TERMINATING)
        assert_equal(self.backend.free_slots,  ncores)
        assert_equal(self.backend.user_queued, 0)
        assert_equal(self.backend.user_run,    0)

        self.core.fetch_output(app)
        assert_equal(app.execution.state, gc3libs.Run.State.TERMINATED)
        assert_equal(self.backend.free_slots,  ncores)
        assert_equal(self.backend.user_queued, 0)
        assert_equal(self.backend.user_run,    0)

    def test_check_app_after_reloading_session(self):
        """Check if we are able to check the status of a job after the
        script which started the job has died.
        """
        tmpdir = tempfile.mkdtemp(prefix=__name__, suffix='.d')
        self.cleanup_file(tmpdir)

        app = gc3libs.Application(
            arguments=['/usr/bin/env'],
            inputs=[],
            outputs=[],
            output_dir=tmpdir,
            stdout="stdout.txt",
            stderr="stderr.txt",
            requested_cores=1, )
        self.core.submit(app)
        self.apps_to_kill.append(app)

        self.cleanup_file(app.execution.lrms_execdir)
        pid = app.execution.lrms_jobid

        # The wrapper process should die and write the final status
        # and the output to a file, so that `Core` will be able to
        # retrieve it.

        # wait until the test job is done, but timeout and raise an error
        # if it takes too much time...
        MAX_WAIT = 10  # seconds
        WAIT = 0.1  # seconds
        waited = 0
        while app.execution.state != gc3libs.Run.State.TERMINATING \
                and waited < MAX_WAIT:
            time.sleep(WAIT)
            waited += WAIT
            self.core.update_job_state(app)

        assert_equal(app.execution.state, gc3libs.Run.State.TERMINATING)
        assert_equal(app.execution.returncode, 0)

    def test_app_argument_with_spaces(self):
        """Check that arguments with spaces are not splitted
        """
        tmpdir = tempfile.mkdtemp(prefix=__name__, suffix='.d')
        self.cleanup_file(tmpdir)

        app = gc3libs.Application(
            arguments=['/bin/ls', '-d', '/ /'],
            inputs=[],
            outputs=[],
            output_dir=tmpdir,
            stdout="stdout.txt",
            stderr="stderr.txt",
            requested_cores=1, )
        self.core.submit(app)
        self.apps_to_kill.append(app)

        self.cleanup_file(app.execution.lrms_execdir)
        MAX_WAIT = 10  # seconds
        WAIT = 0.1  # seconds
        waited = 0
        while app.execution.state != gc3libs.Run.State.TERMINATING \
                and waited < MAX_WAIT:
            time.sleep(WAIT)
            waited += WAIT
            self.core.update_job_state(app)
        assert_equal(app.execution.state, gc3libs.Run.State.TERMINATING)
        assert_equal(app.execution.returncode, 2)

    def test_time_cmd_args(self):
        assert_equal(self.backend.time_cmd, '/usr/bin/time')

    def test_resource_usage(self):
        tmpdir = tempfile.mkdtemp(prefix=__name__, suffix='.d')
        self.cleanup_file(tmpdir)

        app = gc3libs.Application(
            arguments=['/bin/echo', 'Hello', 'World'],
            inputs=[],
            outputs=[],
            output_dir=tmpdir,
            requested_cores=2,
            requested_memory=10 * Memory.MiB, )
        cores_before = self.backend.free_slots
        mem_before = self.backend.available_memory
        self.core.submit(app)
        self.apps_to_kill.append(app)

        cores_after = self.backend.free_slots
        mem_after = self.backend.available_memory
        assert_equal(cores_before, cores_after + 2)
        assert_equal(mem_before, mem_after + app.requested_memory)
        MAX_WAIT = 10  # seconds
        WAIT = 0.1  # seconds
        waited = 0
        while app.execution.state != gc3libs.Run.State.TERMINATING \
                and waited < MAX_WAIT:
            time.sleep(WAIT)
            waited += WAIT
            self.core.update_job_state(app)
        assert_equal(self.backend.free_slots, cores_before)
        avail = self.backend.available_memory
        assert_equal(self.backend.available_memory, mem_before)

    @raises(gc3libs.exceptions.LRMSSubmitError)
    def test_not_enough_cores_usage(self):
        tmpdir = tempfile.mkdtemp(prefix=__name__, suffix='.d')
        self.cleanup_file(tmpdir)
        bigapp = gc3libs.Application(
            arguments=['/bin/echo', 'Hello', 'World'],
            inputs=[],
            outputs=[],
            output_dir=tmpdir,
            requested_cores=self.backend.free_slots,
            requested_memory=10 * Memory.MiB, )
        smallapp = gc3libs.Application(
            arguments=['/bin/echo', 'Hello', 'World'],
            inputs=[],
            outputs=[],
            output_dir=tmpdir,
            requested_cores=1,
            requested_memory=10 * Memory.MiB, )
        self.core.submit(bigapp)
        self.apps_to_kill.append(bigapp)

        self.core.submit(smallapp)
        self.apps_to_kill.append(smallapp)

    @raises(gc3libs.exceptions.LRMSSubmitError)
    def test_not_enough_memory_usage(self):
        tmpdir = tempfile.mkdtemp(prefix=__name__, suffix='.d')
        self.cleanup_file(tmpdir)
        bigapp = gc3libs.Application(
            arguments=['/bin/echo', 'Hello', 'World'],
            inputs=[],
            outputs=[],
            output_dir=tmpdir,
            requested_cores=1,
            requested_memory=self.backend.total_memory, )
        smallapp = gc3libs.Application(
            arguments=['/bin/echo', 'Hello', 'World'],
            inputs=[],
            outputs=[],
            output_dir=tmpdir,
            requested_cores=1,
            requested_memory=10 * Memory.MiB, )
        self.core.submit(bigapp)
        self.apps_to_kill.append(bigapp)

        self.core.submit(smallapp)
        self.apps_to_kill.append(smallapp)


class TestBackendShellcmdCFG(object):
    CONF = """
[resource/localhost_test]
type=shellcmd
transport=local
time_cmd=/usr/bin/time
max_cores=1000
max_cores_per_job=4
max_memory_per_core=2
max_walltime=2
architecture=x64_64
auth=noauth
enabled=True
override=%s

[auth/noauth]
type=none
"""

    def setUp(self):
        self.files_to_remove = []

    def cleanup_file(self, fname):
        self.files_to_remove.append(fname)

    def tearDown(self):
        for fname in self.files_to_remove:
            if os.path.isdir(fname):
                shutil.rmtree(fname)
            elif os.path.exists(fname):
                os.remove(fname)

    def test_override_cfg_flag(self):
        (fd, cfgfile) = tempfile.mkstemp()
        f = os.fdopen(fd, 'w+')
        f.write(TestBackendShellcmdCFG.CONF % "True")
        f.close()
        self.files_to_remove = [cfgfile]

        self.cfg = gc3libs.config.Configuration()
        self.cfg.merge_file(cfgfile)

        self.core = gc3libs.core.Core(self.cfg)
        self.backend = self.core.get_backend('localhost_test')
        # Update resource status
        self.backend.get_resource_status()

        assert self.backend.max_cores < 1000


    def test_do_not_override_cfg_flag(self):
        (fd, cfgfile) = tempfile.mkstemp()
        f = os.fdopen(fd, 'w+')
        f.write(TestBackendShellcmdCFG.CONF % "False")
        f.close()
        self.files_to_remove = [cfgfile]

        self.cfg = gc3libs.config.Configuration()
        self.cfg.merge_file(cfgfile)

        self.core = gc3libs.core.Core(self.cfg)
        self.backend = self.core.get_backend('localhost_test')
        # Update resource status
        self.backend.get_resource_status()

        assert_equal(self.backend.max_cores, 1000)


if __name__ == "__main__":
    import nose
    nose.runmodule()
