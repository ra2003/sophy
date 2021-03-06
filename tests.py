import os
import pickle
import shutil
import sys
import unittest
import uuid

from sophy import *


DB_NAME = 'db-test'
TEST_DIR = 'sophia-test'


def cleanup():
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)


class BaseTestCase(unittest.TestCase):
    databases = (
        ('main', Schema([StringIndex('key')], [StringIndex('value')])),
    )

    def setUp(self):
        cleanup()
        self.env = self.create_env()
        for name, schema in self.databases:
            self.env.add_database(name, schema)
        assert self.env.open()

    def tearDown(self):
        assert self.env.close()
        cleanup()

    def create_env(self):
        return Sophia(TEST_DIR)


class TestConfigurationStability(unittest.TestCase):
    def setUp(self):
        cleanup()
        self.env = Sophia(TEST_DIR)

    def tearDown(self):
        self.env.close()
        cleanup()

    def test_configuration_stability(self):
        self.env.scheduler_threads = 2
        schema = Schema([StringIndex('k'), U16Index('ki')], StringIndex('val'))
        db = self.env.add_database('main', schema)
        db.compression = 'lz4'
        self.env.open()
        self.assertEqual(self.env.scheduler_threads, 2)
        self.assertEqual(db.compression, 'lz4')
        self.assertEqual(db.mmap, 1)
        self.assertEqual(db.sync, 1)

        n = 1000
        for i in range(n):
            db['k%064d' % i, i] = 'v%0256d' % i

        for i in range(n):
            self.assertEqual(db['k%064d' % i, i], 'v%0256d' % i)

        self.assertTrue(self.env.close())

        # Start fresh with new env/db objects and validate config persists.
        env2 = Sophia(TEST_DIR)
        db2 = env2.add_database('main', schema)
        self.assertTrue(env2.open())

        # Scheduler threads does not persist.
        self.assertFalse(env2.scheduler_threads == 2)

        # Compression persists.
        self.assertEqual(db2.compression, 'lz4')

        # We can re-read the data.
        for i in range(n):
            self.assertEqual(db2['k%064d' % i, i], 'v%0256d' % i)

        db2['kx', 0] = 'vx'
        self.assertTrue(env2.close())

        # And re-open our original env.
        self.assertTrue(self.env.open())

        # Compression persists.
        self.assertEqual(db.compression, 'lz4')

        # We can re-read the data using our original db handle.
        for i in range(n):
            self.assertEqual(db['k%064d' % i, i], 'v%0256d' % i)

        self.assertEqual(db['kx', 0], 'vx')


class TestConfiguration(BaseTestCase):
    def test_version(self):
        self.assertEqual(self.env.version, '2.2')

    def test_status(self):
        self.assertEqual(self.env.status, 'online')


class TestBasicOperations(BaseTestCase):
    def test_crud(self):
        db = self.env['main']
        vals = (('huey', 'cat'), ('mickey', 'dog'), ('zaizee', 'cat'))
        for key, value in vals:
            db[key] = value
        for key, value in vals:
            self.assertEqual(db[key], value)
            self.assertTrue(key in db)

        del db['mickey']
        self.assertFalse('mickey' in db)
        self.assertRaises(KeyError, lambda: db['mickey'])

        db['huey'] = 'kitten'
        self.assertEqual(db['huey'], 'kitten')
        db.delete('huey')
        self.assertEqual(db.multi_get(['huey']), [None])

        db.set('k1', 'v1')
        db.set('k2', 'v2')
        self.assertEqual(db.get('k1'), 'v1')
        self.assertEqual(db.get('k2'), 'v2')
        self.assertTrue(db.get('k3') is None)
        self.assertEqual(db.get('k3', 'xx'), 'xx')
        db.delete('k1')
        self.assertTrue(db.get('k1') is None)

    def test_iterables(self):
        db = self.env['main']
        for i in range(4):
            db['k%s' % i] = 'v%s' % i

        items = list(db)
        self.assertEqual(items, [('k0', 'v0'), ('k1', 'v1'), ('k2', 'v2'),
                                 ('k3', 'v3')])
        self.assertEqual(list(db.items()), items)

        self.assertEqual(list(db.keys()), ['k0', 'k1', 'k2', 'k3'])
        self.assertEqual(list(db.values()), ['v0', 'v1', 'v2', 'v3'])
        self.assertEqual(len(db), 4)
        self.assertEqual(db.index_count, 4)

    def test_multi_get_set(self):
        db = self.env['main']
        for i in range(4):
            db['k%s' % i] = 'v%s' % i

        self.assertEqual(db.multi_get(['k0', 'k3', 'k99']), ['v0', 'v3', None])
        self.assertEqual(db.multi_get_dict(['k0', 'k3', 'k99']),
                         {'k0': 'v0', 'k3': 'v3'})

        db.update(k0='v0-e', k3='v3-e', k99='v99-e')
        self.assertEqual(list(db), [('k0', 'v0-e'), ('k1', 'v1'), ('k2', 'v2'),
                                    ('k3', 'v3-e'), ('k99', 'v99-e')])

    def test_get_range(self):
        db = self.env['main']
        for i in range(4):
            db['k%s' % i] = 'v%s' % i

        for k1, k2 in (('k1', 'k2'), (('k1',), 'k2'), ('k1', ('k2',)),
                       (('k1',), ('k2',))):
            self.assertEqual(list(db.get_range(k1, k2)), [
                ('k1', 'v1'), ('k2', 'v2')])

        self.assertEqual(list(db['k1':'k2']), [('k1', 'v1'), ('k2', 'v2')])
        self.assertEqual(list(db['k01':'k21']), [('k1', 'v1'), ('k2', 'v2')])
        self.assertEqual(list(db['k2':]), [('k2', 'v2'), ('k3', 'v3')])
        self.assertEqual(list(db[:'k1']), [('k0', 'v0'), ('k1', 'v1')])
        self.assertEqual(list(db['k2':'kx']), [('k2', 'v2'), ('k3', 'v3')])
        self.assertEqual(list(db['a1':'k1']), [('k0', 'v0'), ('k1', 'v1')])
        self.assertEqual(list(db[:'a1']), [])
        self.assertEqual(list(db['z1':]), [])
        self.assertEqual(list(db[:]), [('k0', 'v0'), ('k1', 'v1'),
                                       ('k2', 'v2'), ('k3', 'v3')])

        self.assertEqual(list(db['k2':'k1']), [('k2', 'v2'), ('k1', 'v1')])
        self.assertEqual(list(db['k21':'k01']), [('k2', 'v2'), ('k1', 'v1')])
        self.assertEqual(list(db['k2'::True]), [('k3', 'v3'), ('k2', 'v2')])
        self.assertEqual(list(db[:'k1':True]), [('k1', 'v1'), ('k0', 'v0')])
        self.assertEqual(list(db['kx':'k2']), [('k3', 'v3'), ('k2', 'v2')])
        self.assertEqual(list(db['k1':'a1']), [('k1', 'v1'), ('k0', 'v0')])
        self.assertEqual(list(db[:'a1':True]), [])
        self.assertEqual(list(db['z1'::True]), [])
        self.assertEqual(list(db[::True]), [('k3', 'v3'), ('k2', 'v2'),
                                            ('k1', 'v1'), ('k0', 'v0')])

        self.assertEqual(list(db['k1':'k2':True]),
                         [('k2', 'v2'), ('k1', 'v1')])
        self.assertEqual(list(db['k2':'k1':True]),
                         [('k2', 'v2'), ('k1', 'v1')])

    def test_open_close(self):
        db = self.env['main']
        db['k1'] = 'v1'
        db['k2'] = 'v2'
        self.assertTrue(self.env.close())
        self.assertTrue(self.env.open())
        self.assertFalse(self.env.open())

        self.assertEqual(db['k1'], 'v1')
        self.assertEqual(db['k2'], 'v2')
        db['k2'] = 'v2-e'

        self.assertTrue(self.env.close())
        self.assertTrue(self.env.open())
        self.assertEqual(db['k2'], 'v2-e')

    def test_transaction(self):
        db = self.env['main']
        db['k1'] = 'v1'
        db['k2'] = 'v2'

        with self.env.transaction() as txn:
            txn_db = txn[db]
            self.assertEqual(txn_db['k1'], 'v1')
            txn_db['k1'] = 'v1-e'
            del txn_db['k2']
            txn_db['k3'] = 'v3'

        self.assertEqual(db['k1'], 'v1-e')
        self.assertRaises(KeyError, lambda: db['k2'])
        self.assertEqual(db['k3'], 'v3')

    def test_rollback(self):
        db = self.env['main']
        db['k1'] = 'v1'
        db['k2'] = 'v2'
        with self.env.transaction() as txn:
            txn_db = txn[db]
            self.assertEqual(txn_db['k1'], 'v1')
            txn_db['k1'] = 'v1-e'
            del txn_db['k2']
            txn.rollback()
            txn_db['k3'] = 'v3'

        self.assertEqual(db['k1'], 'v1')
        self.assertEqual(db['k2'], 'v2')
        self.assertEqual(db['k3'], 'v3')

    def test_multiple_transaction(self):
        db = self.env['main']
        db['k1'] = 'v1'
        txn = self.env.transaction()
        txn.begin()

        txn_db = txn[db]
        txn_db['k2'] = 'v2'
        txn_db['k3'] = 'v3'

        txn2 = self.env.transaction()
        txn2.begin()

        txn2_db = txn2[db]
        txn2_db['k1'] = 'v1-e'
        txn2_db['k4'] = 'v4'

        txn.commit()
        txn2.commit()

        self.assertEqual(list(db), [('k1', 'v1-e'), ('k2', 'v2'), ('k3', 'v3'),
                                    ('k4', 'v4')])

    def test_transaction_conflict(self):
        db = self.env['main']
        db['k1'] = 'v1'
        txn = self.env.transaction()
        txn.begin()

        txn_db = txn[db]
        txn_db['k2'] = 'v2'
        txn_db['k3'] = 'v3'

        txn2 = self.env.transaction()
        txn2.begin()

        txn2_db = txn2[db]
        txn2_db['k2'] = 'v2-e'

        # txn is not finished, waiting for concurrent txn to finish.
        self.assertRaises(SophiaError, txn2.commit)
        txn.commit()

        # txn2 was rolled back by another concurrent txn.
        self.assertRaises(SophiaError, txn2.commit)

        # Only changes from txn are present.
        self.assertEqual(list(db), [('k1', 'v1'), ('k2', 'v2'), ('k3', 'v3')])

    def test_cursor(self):
        db = self.env['main']
        db.update(k1='v1', k2='v2', k3='v3')

        curs = db.cursor()
        self.assertEqual(
            list(curs),
            [('k1', 'v1'), ('k2', 'v2'), ('k3', 'v3')])

        curs = db.cursor(order='<')
        self.assertEqual(
            list(curs),
            [('k3', 'v3'), ('k2', 'v2'), ('k1', 'v1')])


class TestGetRangeNormalizeValues(BaseTestCase):
    databases = (
        ('single_u', Schema(StringIndex('key'), U8Index('value'))),
        ('single_b', Schema(BytesIndex('key'), U8Index('value'))),
        ('multi_u', Schema([StringIndex('k0'), StringIndex('k1')],
                           [U8Index('value')])),
        ('multi_b', Schema([BytesIndex('k0'), BytesIndex('k1')],
                           [U8Index('value')])),
        ('multi_ub', Schema([StringIndex('k0'), BytesIndex('k1')],
                            [U8Index('value')])),
    )

    def test_get_range_normalized_single(self):
        def assertRange(db, start, stop, exp):
            self.assertEqual([v for _, v in db.get_range(start, stop)], exp)

        for db_name in ('single_u', 'single_b'):
            db = self.env[db_name]
            for i in range(10):
                db['k%s' % i] = i

            assertRange(db, 'k2', 'k45', [2, 3, 4])
            assertRange(db, b'k2', b'k45', [2, 3, 4])

    def test_get_range_normalized_multi(self):
        def assertRange(db, start, stop, exp):
            self.assertEqual([v for _, v in db.get_range(start, stop)], exp)

        for db_name in ('multi_u', 'multi_b', 'multi_ub'):
            db = self.env[db_name]
            for i in range(10):
                db['k%s' % i, 'x%s' % i] = i

            assertRange(db, ('k2', 'x2'), ('k45', 'x45'), [2, 3, 4])
            assertRange(db, (b'k2', b'x2'), (b'k45', b'x45'), [2, 3, 4])
            assertRange(db, (b'k2', 'x2'), (b'k45', 'x45'), [2, 3, 4])
            assertRange(db, ('k2', b'x2'), ('k45', b'x45'), [2, 3, 4])


class TestValidation(BaseTestCase):
    databases = (
        ('single', Schema(StringIndex('key'), U8Index('value'))),
        ('multi', Schema((U8Index('k1'), StringIndex('k2')),
                         (U8Index('v1'), StringIndex('v2')))),
    )

    def test_validate_single(self):
        db = self.env['single']
        db.set('k1', 1)
        db.set(('k2',), 2)
        db.set('k3', (3,))
        db.set(('k4',), (4,))
        for i in range(1, 5):
            self.assertTrue(db.exists('k%s' % i))
            self.assertTrue(db.exists(('k%s' % i,)))
            self.assertEqual(db.get('k%s' % i), i)
            self.assertEqual(db.get(('k%s' % i,)), i)

        # Invalid key- and value-lengths.
        self.assertRaises(ValueError, db.set, ('k1', 1), 100)
        self.assertRaises(ValueError, db.set, 'k1', (101, 102))
        self.assertRaises(ValueError, db.get, ('k1', 1))
        self.assertRaises(ValueError, db.exists, ('k1', 1))

        # Bulk-operations.
        self.assertRaises(ValueError, db.update, {('k1', 1): 100})
        self.assertRaises(ValueError, db.update, {'k1': (101, 102)})
        self.assertRaises(ValueError, db.multi_get, [('k1', 1)])
        self.assertRaises(ValueError, db.multi_get_dict, [('k1', 1)])
        self.assertRaises(ValueError, db.multi_delete, [('k1', 1)])

        # No bogus data was written.
        self.assertEqual(db['k1'], 1)

    def test_validate_multi(self):
        db = self.env['multi']
        db.set((1, 'k1'), (11, 'v1'))

        self.assertTrue(db.exists((1, 'k1')))
        self.assertEqual(db.get((1, 'k1')), (11, 'v1'))

        # Invalid key- and value-lengths.
        self.assertRaises(ValueError, db.set, 1, (101, 'v1'))
        self.assertRaises(ValueError, db.set, (1, 'k1'), 102)
        self.assertRaises(ValueError, db.set, (1, 'k1', 2), (101, 'v1', 102))
        self.assertRaises(ValueError, db.get, 1)
        self.assertRaises(ValueError, db.get, (1, 'k1', 101))
        self.assertRaises(ValueError, db.exists, 1)
        self.assertRaises(ValueError, db.exists, (1, 'k1', 101))

        # Bulk-operations.
        self.assertRaises(ValueError, db.update, {1: (100, 'v1')})
        self.assertRaises(ValueError, db.update, {(1, 'k1'): 100})
        self.assertRaises(ValueError, db.update, {(1, 'k1', 2): 100})
        self.assertRaises(ValueError, db.update, {(1, 'k1', 2): (12, 'v1', 2)})
        for k in (1, (1, 'k1', 2)):
            self.assertRaises(ValueError, db.multi_get, [k])
            self.assertRaises(ValueError, db.multi_get_dict, [k])
            self.assertRaises(ValueError, db.multi_delete, [k])

        # No bogus data was written.
        self.assertEqual(db[1, 'k1'], (11, 'v1'))


class TestCursorOptions(BaseTestCase):
    databases = (
        ('main', Schema(StringIndex('key'), U16Index('value'))),
        ('secondary', Schema([StringIndex('key_a'), StringIndex('key_b')],
                             U8Index('value'))),
    )

    def test_cursor_options(self):
        db = self.env['main']

        k_tmpl = 'log:%08x:%08x:record%s'
        for i in range(16):
            db[k_tmpl % (i, i, i)] = i

        def assertCursor(cursor, indexes):
            self.assertEqual(list(cursor), [
                (k_tmpl % (i, i, i), i) for i in indexes])

        # Default ordering.
        assertCursor(db.cursor(), range(16))

        # Reverse ordering.
        assertCursor(db.cursor(order='<='), reversed(range(16)))

        # Default ordering with prefix.
        assertCursor(db.cursor(prefix='log:'), range(16))

        # Reverse ordering with prefix. Note that we have to specify a
        # start-key, which is probably indicative of a bug (see sophia #167).
        assertCursor(db.cursor(order='<=', prefix='log:'), [])  # XXX: bug?
        assertCursor(db.cursor(order='<=', prefix='log:', key='m'),
                     reversed(range(16)))

        # Use the following key as a starting-point.
        key = k_tmpl % (12, 12, 12)

        # Iterate up from log:0000000c:0000000c:recordc (inclusive).
        assertCursor(db.cursor(prefix='log:', key=key), range(12, 16))
        # Iterate up from log:0000000c:0000000c:recordc (exclusive).
        assertCursor(db.cursor(prefix='log:', key=key, order='>'),
                     range(13, 16))
        # Iterate down from log:0000000c:0000000c:recordc (inclusive).
        assertCursor(db.cursor(prefix='log:', key=key, order='<='),
                     reversed(range(13)))
        # Iterate down from log:0000000c:0000000c:recordc (exclusive).
        assertCursor(db.cursor(prefix='log:', key=key, order='<'),
                     reversed(range(12)))

    def test_cursor_options_multikey(self):
        db = self.env['secondary']
        ka_tmpl = 'log:%08x'
        kb_tmpl = 'evt:%08x'
        for i in range(4):
            for j in range(4):
                db[ka_tmpl % i, kb_tmpl % j] = (4 * i) + j

        def assertCursor(cursor, indexes):
            self.assertEqual(list(cursor), [
                ((ka_tmpl % (i // 4), kb_tmpl % (i % 4)), i) for i in indexes])

        # Default and reverse ordering.
        assertCursor(db.cursor(), range(16))
        assertCursor(db.cursor(order='<='), reversed(range(16)))

        # Default and reverse ordering with prefix.
        assertCursor(db.cursor(prefix='log:'), range(16))
        assertCursor(db.cursor(order='<=', prefix='log:'), [])  # XXX: bug?
        assertCursor(db.cursor(order='<=', prefix='log:', key=('m', '')),
                     reversed(range(16)))

        ka = ka_tmpl % 2
        kb = kb_tmpl % 2
        for prefix in (None, 'log:'):
            assertCursor(db.cursor(prefix=prefix, key=(ka, kb)), range(10, 16))
            assertCursor(db.cursor(prefix=prefix, key=(ka, kb), order='>'),
                         range(11, 16))
            assertCursor(db.cursor(prefix=prefix, key=(ka, kb), order='<='),
                         reversed(range(11)))
            assertCursor(db.cursor(prefix=prefix, key=(ka, kb), order='<'),
                         reversed(range(10)))

        assertCursor(db.cursor(prefix=ka), range(8, 12))
        assertCursor(db.cursor(prefix=ka, order='<='), [])  # XXX: bug?

        # The second key does not factor into the prefix.
        assertCursor(db.cursor(prefix='evt:'), [])
        assertCursor(db.cursor(prefix='evt:', order='<='), [])


class TestMultipleDatabases(BaseTestCase):
    databases = (
        ('main', Schema([StringIndex('key')], [StringIndex('value')])),
        ('secondary', Schema([StringIndex('key')], [StringIndex('value')])),
    )

    def test_multiple_databases(self):
        main = self.env['main']
        scnd = self.env['secondary']

        main.update(k1='v1', k2='v2', k3='v3')
        scnd.update(k1='v1_2', k2='v2_2', k3='v3_2')

        del main['k1']
        del scnd['k2']
        self.assertRaises(KeyError, lambda: main['k1'])
        self.assertRaises(KeyError, lambda: scnd['k2'])

        self.assertEqual(list(main), [('k2', 'v2'), ('k3', 'v3')])
        self.assertEqual(list(scnd), [('k1', 'v1_2'), ('k3', 'v3_2')])

    def test_multiple_db_txn(self):
        main = self.env['main']
        scnd = self.env['secondary']

        main.update(k1='v1', k2='v2')
        scnd.update(k1='v1_2', k2='v2_2')

        with self.env.transaction() as txn:
            t_main = txn[main]
            t_scnd = txn[scnd]

            del t_main['k1']
            t_main['k2'] = 'v2-e'
            t_main['k3'] = 'v3'
            del t_scnd['k2']
            t_scnd['k1'] = 'v1_2-e'

        self.assertEqual(list(main), [('k2', 'v2-e'), ('k3', 'v3')])
        self.assertEqual(list(scnd), [('k1', 'v1_2-e')])

        with self.env.transaction() as txn:
            t_main = txn[main]
            t_scnd = txn[scnd]
            del t_main['k2']
            t_scnd['k3'] = 'v3_2'
            txn.rollback()
            self.assertEqual(t_main['k2'], 'v2-e')
            self.assertRaises(KeyError, lambda: t_scnd['k3'])
            t_main['k3'] = 'v3-e'
            t_scnd['k2'] = 'v2_2-e'

        self.assertEqual(list(main), [('k2', 'v2-e'), ('k3', 'v3-e')])
        self.assertEqual(list(scnd), [('k1', 'v1_2-e'), ('k2', 'v2_2-e')])

    def test_open_close(self):
        self.assertTrue(self.env.close())
        self.assertTrue(self.env.open())

    def test_add_db(self):
        schema = Schema([StringIndex('key')], [StringIndex('value')])
        self.assertRaises(SophiaError, self.env.add_database, 'db-3', schema)
        self.env.close()

        self.env.add_database('db-3', schema)
        self.env.open()
        db = self.env['db-3']
        db['k1'] = 'v1'
        self.assertEqual(db['k1'], 'v1')


class TestMultiKeyValue(BaseTestCase):
    databases = (
        ('main',
         Schema([U64Index('year'), U32Index('month'), U16Index('day'),
                 StringIndex('event')],
                [StringIndex('source'), StringIndex('data')])),
        ('numbers',
         Schema([U16RevIndex('key')],
                [U16Index('v1'), U16Index('v2'), U16Index('v3'),
                 U16Index('v4'), U8Index('v5')])),
    )
    test_data = (
        ((2017, 1, 1, 'holiday'), ('us', 'new years')),
        ((2017, 5, 29, 'holiday'), ('us', 'memorial day')),
        ((2017, 7, 4, 'holiday'), ('us', 'independence day')),
        ((2017, 9, 4, 'holiday'), ('us', 'labor day')),
        ((2017, 11, 23, 'holiday'), ('us', 'thanksgiving')),
        ((2017, 12, 25, 'holiday'), ('us', 'christmas')),
        ((2017, 7, 1, 'birthday'), ('private', 'huey')),
        ((2017, 5, 1, 'birthday'), ('private', 'mickey')),
    )

    def setUp(self):
        super(TestMultiKeyValue, self).setUp()
        self.db = self.env['main']

    def test_multi_key_crud(self):
        for key, value in self.test_data:
            self.db[key] = value

        for key, value in self.test_data:
            self.assertEqual(self.db[key], value)

        del self.db[2017, 11, 12, 'holiday']
        self.assertRaises(KeyError, lambda: self.db[2017, 11, 12, 'holiday'])

    def test_iteration(self):
        for key, value in self.test_data:
            self.db[key] = value

        self.assertEqual(list(self.db), sorted(self.test_data))
        self.assertEqual(list(self.db.items()), sorted(self.test_data))
        self.assertEqual(list(self.db.keys()),
                         sorted(key for key, _ in self.test_data))
        self.assertEqual(list(self.db.values()),
                         [value for _, value in sorted(self.test_data)])

    def test_update_multiget(self):
        self.db.update(dict(self.test_data))
        events = ((2017, 1, 1, 'holiday'),
                  (2017, 12, 25, 'holiday'),
                  (2017, 7, 1, 'birthday'))
        self.assertEqual(self.db.multi_get(events), [
            ('us', 'new years'),
            ('us', 'christmas'),
            ('private', 'huey')])
        self.assertEqual(self.db.multi_get_dict(events), {
            (2017, 1, 1, 'holiday'): ('us', 'new years'),
            (2017, 12, 25, 'holiday'): ('us', 'christmas'),
            (2017, 7, 1, 'birthday'): ('private', 'huey')})

    def test_ranges(self):
        self.db.update(dict(self.test_data))
        items = self.db[(2017, 2, 1, ''):(2017, 6, 1, '')]
        self.assertEqual(list(items), [
            ((2017, 5, 1, 'birthday'), ('private', 'mickey')),
            ((2017, 5, 29, 'holiday'), ('us', 'memorial day'))])

        items = self.db[:(2017, 2, 1, '')]
        self.assertEqual(list(items), [
            ((2017, 1, 1, 'holiday'), ('us', 'new years'))])

        items = self.db[(2017, 11, 1, '')::True]
        self.assertEqual(list(items), [
            ((2017, 12, 25, 'holiday'), ('us', 'christmas')),
            ((2017, 11, 23, 'holiday'), ('us', 'thanksgiving'))])

    def test_rev_indexes(self):
        nums = self.env['numbers']
        for i in range(100):
            key, v1, v2, v3, v4, v5 = range(i, 6 + i)
            nums[key] = (v1, v2, v3, v4, v5)

        self.assertEqual(len(nums), 100)
        self.assertEqual(nums[0], (1, 2, 3, 4, 5))
        self.assertEqual(nums[99], (100, 101, 102, 103, 104))

        self.assertEqual(list(nums[:2]), [])
        self.assertEqual(list(nums[2:]), [
            (2, (3, 4, 5, 6, 7)),
            (1, (2, 3, 4, 5, 6)),
            (0, (1, 2, 3, 4, 5))])

        self.assertEqual(list(nums.keys())[:3], [99, 98, 97])
        self.assertEqual(list(nums.values())[:3], [
            (100, 101, 102, 103, 104),
            (99, 100, 101, 102, 103),
            (98, 99, 100, 101, 102)])

    def test_bounds(self):
        nums = self.env['numbers']
        nums[0] = (0, 0, 0, 0, 0)
        self.assertEqual(nums[0], (0, 0, 0, 0, 0))

        nums[1] = (0, 0, 0, 0, 255)
        self.assertEqual(nums[1], (0, 0, 0, 0, 255))


class TestEventSchema(BaseTestCase):
    databases = (
        ('main',
         Schema([U64Index('timestamp'), StringIndex('type')],
                [SerializedIndex('data', pickle.dumps, pickle.loads)])),
    )

    def setUp(self):
        super(TestEventSchema, self).setUp()
        self.db = self.env['main']

    def test_events_examples(self):
        ts = lambda i: 1000000000 + i

        self.db[ts(1), 'init'] = {'msg': 'starting up'}
        self.db[ts(2), 'info'] = {'msg': 'info1'}
        self.db[ts(3), 'info'] = {'msg': 'info2'}
        self.db[ts(3), 'warning'] = {'msg': 'warn1'}
        self.db[ts(4), 'info'] = {'msg': 'info3'}
        self.db[ts(4), 'error'] = {'msg': 'error1'}

        self.assertEqual(self.db[ts(3), 'info'], {'msg': 'info2'})
        self.assertEqual(self.db[ts(4), 'info'], {'msg': 'info3'})
        self.assertRaises(KeyError, lambda: self.db[ts(4), 'xx'])

        start = (ts(1), '')
        stop = (ts(3), '')
        data = self.db.get_range(start=start, stop=stop)
        self.assertEqual(list(data), [
            ((ts(1), 'init'), {'msg': 'starting up'}),
            ((ts(2), 'info'), {'msg': 'info1'}),
        ])

        stop = (ts(4), 'f')
        data = self.db.get_range(start=start, stop=stop, reverse=True)
        self.assertEqual(list(data), [
            ((ts(4), 'error'), {'msg': 'error1'}),
            ((ts(3), 'warning'), {'msg': 'warn1'}),
            ((ts(3), 'info'), {'msg': 'info2'}),
            ((ts(2), 'info'), {'msg': 'info1'}),
            ((ts(1), 'init'), {'msg': 'starting up'}),
        ])

        curs = self.db.cursor(order='<', key=(ts(3), 'info'), values=False)
        self.assertEqual(list(curs), [(ts(2), 'info'), (ts(1), 'init')])

        curs = self.db.cursor(order='>=', key=(ts(3), 'info'), values=False)
        self.assertEqual(list(curs), [
            (ts(3), 'info'),
            (ts(3), 'warning'),
            (ts(4), 'error'),
            (ts(4), 'info')])


class TestMultiKeyValue(BaseTestCase):
    databases = (
        ('main',
         Schema([U32Index('a'), U32Index('b'), U32Index('c')],
                [U32Index('value')])),
        ('secondary',
         Schema([BytesIndex('a'), U32Index('b')],
                [U32Index('value')])),
    )

    def setUp(self):
        super(TestMultiKeyValue, self).setUp()
        self.db = self.env['main']

    def test_cursor_ops(self):
        for i in range(10):
            for j in range(5):
                for k in range(3):
                    self.db[i, j, k] = i * j * k

        data = self.db[(3, 3, 0):(4, 2, 1)]
        self.assertEqual(list(data), [
            ((3, 3, 0), 0),
            ((3, 3, 1), 9),
            ((3, 3, 2), 18),
            ((3, 4, 0), 0),
            ((3, 4, 1), 12),
            ((3, 4, 2), 24),
            ((4, 0, 0), 0),
            ((4, 0, 1), 0),
            ((4, 0, 2), 0),
            ((4, 1, 0), 0),
            ((4, 1, 1), 4),
            ((4, 1, 2), 8),
            ((4, 2, 0), 0),
            ((4, 2, 1), 8),
        ])

    def test_ordering_string(self):
        db = self.env['secondary']
        db['a', 0] = 1
        db['b', 1] = 2
        db['b', 0] = 3
        db['d', 0] = 4
        db['c', 9] = 5
        db['c', 3] = 6

        data = list(db[(b'b', 0):(b'\xff', 5)])
        self.assertEqual(data, [
            ((b'b', 0), 3),
            ((b'b', 1), 2),
            ((b'c', 3), 6),
            ((b'c', 9), 5),
            ((b'd', 0), 4)])

        data = list(db[(b'\x00', 0):(b'b', 5)])
        self.assertEqual(data, [
            ((b'a', 0), 1),
            ((b'b', 0), 3),
            ((b'b', 1), 2)])

        data = list(db[(b'bb', 0):(b'cc', 5)])
        self.assertEqual(data, [
            ((b'c', 3), 6),
            ((b'c', 9), 5)])


class TestStringVsBytes(BaseTestCase):
    databases = (
        ('string',
         Schema([StringIndex('key')],
                [StringIndex('value')])),
        ('bytes',
         Schema([BytesIndex('key')],
                [BytesIndex('value')])),
    )

    def setUp(self):
        super(TestStringVsBytes, self).setUp()
        self.sdb = self.env['string']
        self.bdb = self.env['bytes']

    def test_string_encoding(self):
        self.sdb[u'k1'] = u'v1'
        self.assertEqual(self.sdb[u'k1'], u'v1')

        smartquotes = u'\u2036hello\u2033'
        encoded = smartquotes.encode('utf-8')
        self.sdb[smartquotes] = smartquotes
        self.assertEqual(self.sdb[encoded], smartquotes)

        self.bdb[encoded] = encoded
        self.assertEqual(self.bdb[encoded], encoded)

        self.bdb[b'\xff'] = b'\xff'
        self.assertEqual(self.bdb[b'\xff'], b'\xff')


class TestSerializedIndex(BaseTestCase):
    databases = (
        ('main',
         Schema(StringIndex('key'),
                SerializedIndex('value', pickle.dumps, pickle.loads))),
    )

    def setUp(self):
        super(TestSerializedIndex, self).setUp()
        self.db = self.env['main']

    def test_serialize_deserialize(self):
        self.db['k1'] = 'v1'
        self.db['k2'] = {'foo': 'bar', 'baz': 1}
        self.db['k3'] = None

        self.assertEqual(self.db['k1'], 'v1')
        self.assertEqual(self.db['k2'], {'foo': 'bar', 'baz': 1})
        self.assertTrue(self.db['k3'] is None)
        self.assertRaises(KeyError, lambda: self.db['k4'])

        data = list(self.db['k1':'k2'])
        self.assertEqual(data, [
            ('k1', 'v1'),
            ('k2', {'foo': 'bar', 'baz': 1})])


class TestSerializedIndexImplementations(BaseTestCase):
    databases = (
        ('json',
         Schema(StringIndex('key'), JsonIndex('value'))),
        ('pickle',
         Schema(StringIndex('key'), PickleIndex('value'))),
        ('uuid',
         Schema(UUIDIndex('key'), StringIndex('value'))),
    )

    def setUp(self):
        super(TestSerializedIndexImplementations, self).setUp()
        self.jdb = self.env['json']
        self.pdb = self.env['pickle']

    def _do_test(self, db):
        db['k1'] = 'v1'
        db['k2'] = {'foo': 'bar', 'baz': 1}
        db['k3'] = None

        self.assertEqual(db['k1'], 'v1')
        self.assertEqual(db['k2'], {'foo': 'bar', 'baz': 1})
        self.assertTrue(db['k3'] is None)
        self.assertRaises(KeyError, lambda: db['k4'])

        data = list(db['k1':'k2'])
        self.assertEqual(data, [
            ('k1', 'v1'),
            ('k2', {'foo': 'bar', 'baz': 1})])

    def test_json(self):
        self._do_test(self.jdb)

    def test_pickle(self):
        self._do_test(self.pdb)

    def test_uuid(self):
        u1 = uuid.uuid4()
        u2 = uuid.uuid4()

        db = self.env['uuid']
        db[u1] = 'u1'
        db[u2] = 'u2'
        self.assertEqual(db[u1], 'u1')
        self.assertEqual(db[u2], 'u2')

        keys = list(db.keys())
        self.assertEqual(set(keys), set((u1, u2)))


if __name__ == '__main__':
    unittest.main(argv=sys.argv)
