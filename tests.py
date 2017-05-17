import sys
import unittest
import socket
import threading
import logging

try:
    from hashlib import md5
except ImportError:
    from md5 import md5

import radius


logging.basicConfig(
    stream=sys.stderr,
    # Change level to DEBUG here if you need to.
    level=logging.CRITICAL,
    format='%(thread)d: %(message)s'
)
LOGGER = logging.getLogger(__name__)

TEST_SECRET = b's3cr3t'
TEST_HOST = 'localhost'
TEST_PORT = 1812


def create_reply(m1, code=radius.CODE_ACCESS_REJECT, attributes={}):
    """
    Helper function.
    """
    m1_data = m1.pack()
    m2 = radius.Message(TEST_SECRET, code, id=m1.id,
                        authenticator=m1.authenticator, attributes=attributes)
    # Pack the second message with an invalid authenticator.
    m2_data = m2.pack()
    # Then calculate the correct authenticator using the message data.
    # Replace the authenticator with the correct one.
    m2.authenticator = md5(
        m2_data[:4] + m1.authenticator + m2_data[20:] + TEST_SECRET
    ).digest()
    return m2


class AttributesTestCase(unittest.TestCase):
    """
    Test attribute multi-dict.
    """

    def test_set_get_item(self):
        """Test setting and getting items."""
        a = radius.Attributes()

        # Cannot use invalid radius codes or names.
        with self.assertRaises(ValueError):
            a[128] = b'bar'

        with self.assertRaises(ValueError):
            a['foo'] = b'bar'

        with self.assertRaises(KeyError):
            a['User-Name']

        a['User-Name'] = b'foobar'
        self.assertEqual([b'foobar'], a[radius.ATTR_USER_NAME])

    def test_init_update(self):
        """Test __init__ and update."""
        with self.assertRaises(ValueError):
            a = radius.Attributes({'foo': b'bar'})

        a = radius.Attributes({'User-Name': b'foobar'})
        self.assertEqual([b'foobar'], a['User-Name'])

        with self.assertRaises(ValueError):
            a.update({'foo': b'bar'})

        a.update({'User-Password': b'raboof'})
        self.assertEqual([b'foobar'], a['User-Name'])
        self.assertEqual([b'raboof'], a['User-Password'])

    def test_un_pack(self):
        """Test packing and unpacking attributes."""
        a = radius.Attributes()
        a['User-Name'] = b'foobar'
        a['User-Password'] = b'raboof'
        data = a.pack()
        self.assertEqual(16, len(data))
        b = radius.Attributes.unpack(data)
        self.assertEqual(2, len(b))
        self.assertEqual([b'foobar'], b['User-Name'])
        self.assertEqual([b'raboof'], b['User-Password'])


class MessageTestCase(unittest.TestCase):
    """
    Test message packing and unpacking.
    """

    def test_message(self):
        """Test message initialization."""
        m = radius.Message(radius.CODE_ACCESS_REQUEST, TEST_SECRET,
                           attributes={})
        self.assertLess(0, m.id)
        self.assertGreater(256, m.id)
        self.assertEqual(16, len(m.authenticator))
        self.assertIsInstance(m.attributes, radius.Attributes)

    def test_un_pack(self):
        """Test packing and unpacking messages."""
        m = radius.access_request(TEST_SECRET, b'foo', b'bar')
        d = m.pack()
        self.assertEqual(43, len(d))

        u = radius.Message.unpack(TEST_SECRET, d)
        self.assertEqual(radius.CODE_ACCESS_REQUEST, u.code)
        self.assertEqual(m.id, u.id)
        self.assertEqual(m.authenticator, u.authenticator)

        # Extra data should not prevent unpacking.
        radius.Message.unpack(TEST_SECRET, d + b'0')

    def test_verify(self):
        """Test response verification."""
        m1 = radius.access_request(TEST_SECRET, b'foo', b'bar')
        m2 = create_reply(m1)

        # Verify should now succeed.
        m2 = m1.verify(m2.pack())
        self.assertIsInstance(m2, radius.Message)

        # Should fail with incrrect id
        m2.id += 1
        with self.assertRaises(AssertionError):
            m1.verify(m2.pack())

        # Should fail with incorrect authenticator.
        m2.authenticator = b'0' * 16
        with self.assertRaises(AssertionError):
            m1.verify(m2.pack())


class RadiusTestCase(unittest.TestCase):
    """Test the RADIUS client."""

    def setUp(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', 0))
        self.port = self.sock.getsockname()[1]

    def tearDown(self):
        self.sock.close()

    def test_connect(self):
        """Test connecting."""
        r = radius.Radius(TEST_SECRET, host='localhost', port=self.port)
        with r.connect() as c:
            c.send(b'hello?')

        self.assertEqual(b'hello?', self.sock.recv(32))

    def test_failure(self):
        """Test sending a message and receiving a reject reply."""
        def _reply_to_client():
            """Thread to act as server."""
            recv, addr = self.sock.recvfrom(4096)
            m1 = radius.Message.unpack(TEST_SECRET, recv)
            m2 = create_reply(m1)
            self.sock.sendto(m2.pack(), addr)

        t = threading.Thread(target=_reply_to_client)
        t.start()

        r = radius.Radius(TEST_SECRET, host='localhost', port=self.port)
        self.assertFalse(r.authenticate('username', 'password'))

    def test_success(self):
        """Test sending a message and receiving an accept reply."""
        def _reply_to_client():
            """Thread to act as server."""
            data, addr = self.sock.recvfrom(4096)
            m1 = radius.Message.unpack(TEST_SECRET, data)
            m2 = create_reply(m1, radius.CODE_ACCESS_ACCEPT)
            self.sock.sendto(m2.pack(), addr)

        t = threading.Thread(target=_reply_to_client)
        t.start()

        r = radius.Radius(TEST_SECRET, host='localhost', port=self.port)
        self.assertTrue(r.authenticate('username', 'password'))

    def test_challenge(self):
        """Test sending a message and receiving an challenge reply."""
        def _reply_to_client():
            """Thread to act as server."""
            data, addr = self.sock.recvfrom(4096)
            m1 = radius.Message.unpack(TEST_SECRET, data)
            m2 = create_reply(m1, radius.CODE_ACCESS_CHALLENGE, attributes={
                'Reply-Message': b'Message one',
                'State': b'Indiana',
            })
            self.sock.sendto(m2.pack(), addr)

        t = threading.Thread(target=_reply_to_client)
        t.start()

        r = radius.Radius(TEST_SECRET, host='localhost', port=self.port)
        try:
            r.authenticate('username', 'password')
        except radius.ChallengeResponse as e:
            self.assertEqual(1, len(e.messages))
            self.assertEqual([b'Message one'], e.messages)
            self.assertEqual([b'Indiana'], e.state)
        else:
            self.fail('ChallengeResponse not raised')


class RadcryptTestCase(unittest.TestCase):
    """
      On transmission, the password is hidden.  The password is first
      padded at the end with nulls to a multiple of 16 octets.  A one-
      way MD5 hash is calculated over a stream of octets consisting of
      the shared secret followed by the Request Authenticator.  This
      value is XORed with the first 16 octet segment of the password and
      placed in the first 16 octets of the String field of the User-
      Password Attribute.

      If the password is longer than 16 characters, a second one-way MD5
      hash is calculated over a stream of octets consisting of the
      shared secret followed by the result of the first xor.  That hash
      is XORed with the second 16 octet segment of the password and
      placed in the second 16 octets of the String field of the User-
      Password Attribute.

      If necessary, this operation is repeated, with each xor result
      being used along with the shared secret to generate the next hash
      to xor the next segment of the password, to no more than 128
      characters.

      The method is taken from the book "Network Security" by Kaufman,
      Perlman and Speciner [9] pages 109-110.  A more precise
      explanation of the method follows:

      Call the shared secret S and the pseudo-random 128-bit Request
      Authenticator RA.  Break the password into 16-octet chunks p1, p2,
      etc.  with the last one padded at the end with nulls to a 16-octet
      boundary.  Call the ciphertext blocks c(1), c(2), etc.  We'll need
      intermediate values b1, b2, etc.

         b1 = MD5(S + RA)       c(1) = p1 xor b1
         b2 = MD5(S + c(1))     c(2) = p2 xor b2
                .                       .
                .                       .
                .                       .
         bi = MD5(S + c(i-1))   c(i) = pi xor bi

      The String will contain c(1)+c(2)+...+c(i) where + denotes
      concatenation.
    """

    authenticator = b'\xa0\xdb7\xe2\x1f1\x18-op\xff>&A\xb0g'

    def test_radcrypt_small(self):
        'Test a password shorter than 16 octets.'
        SMALL_PASS = b'I3Zl@"42Xs%^[nk'
        SMALL_CRYPT = b'\xdc\xf7V\x82\xeb\xa8Zm\x1b\x92\xb3\xa3\x06\x02\xbc\x16'
        c = radius.radcrypt(TEST_SECRET, self.authenticator, SMALL_PASS)
        self.assertEqual(c, SMALL_CRYPT)

    def test_radcrypt_large(self):
        'Test a password longer than 16 octets.'
        LARGE_PASS = b'`0T8/Ub\tojdP;\rc:L}#_hOF'
        LARGE_CRYPT = b'\xf5\xf4X\xd6\x84\xdf\x0cV,\x8b\xf2\xadfa\xb4,S\xef\x0f\x908\xfcH\x9a\xe9r\xcc\xd0\x07\x84\xdc\x98'
        c = radius.radcrypt(TEST_SECRET, self.authenticator, LARGE_PASS)
        self.assertEqual(c, LARGE_CRYPT)


if __name__ == '__main__':
    unittest.main()
