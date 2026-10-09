package fr.theo.hueserver;

import java.io.IOException;
import java.net.DatagramSocket;
import org.bouncycastle.tls.UDPTransport;

/** Bounds unauthenticated handshake fragment declarations before BC reassembly allocates. */
final class BoundedUdpTransport extends UDPTransport {
    private static final int MAX_HANDSHAKE_MESSAGE = 4096;
    BoundedUdpTransport(DatagramSocket socket) throws IOException { super(socket, 1200); }

    @Override public int receive(byte[] bytes, int offset, int length, int waitMillis) throws IOException {
        int received = super.receive(bytes, offset, length, waitMillis);
        if (received >= 0 && !validDatagram(bytes, offset, received)) throw new IOException("handshake-failed");
        return received;
    }

    static boolean validDatagram(byte[] bytes, int offset, int length) {
        if (bytes == null || offset < 0 || length < 13 || offset > bytes.length - length) return false;
        int end = offset + length;
        while (offset < end) {
            if (end - offset < 13) return false;
            int recordLength = uint16(bytes, offset + 11);
            int body = offset + 13;
            if (recordLength > end - body) return false;
            int recordEnd = body + recordLength;
            // Epoch 0 is plaintext. Epoch >=1 is integrity checked by the DTLS record layer.
            if ((bytes[offset] & 255) == 22 && uint16(bytes, offset + 3) == 0) {
                while (body < recordEnd) {
                    if (recordEnd - body < 12) return false;
                    int declared = uint24(bytes, body + 1);
                    int fragmentOffset = uint24(bytes, body + 6);
                    int fragmentLength = uint24(bytes, body + 9);
                    if (declared > MAX_HANDSHAKE_MESSAGE || fragmentOffset > declared
                            || fragmentLength > declared - fragmentOffset
                            || fragmentLength > recordEnd - body - 12) return false;
                    body += 12 + fragmentLength;
                }
            }
            offset = recordEnd;
        }
        return true;
    }
    private static int uint16(byte[] bytes, int index) {
        return ((bytes[index] & 255) << 8) | (bytes[index + 1] & 255);
    }
    private static int uint24(byte[] bytes, int index) {
        return ((bytes[index] & 255) << 16) | uint16(bytes, index + 1);
    }
}
