package fr.theo.hueserver;

import java.util.Arrays;

/** Tests the pre-reassembly DTLS receive guard using synthetic non-secret datagrams. */
public final class DatagramGuardTests {
    private DatagramGuardTests() { }
    static boolean valid(byte[] packet) throws Exception {
        Class<?> guard;
        try { guard = Class.forName("fr.theo.hueserver.BoundedUdpTransport"); }
        catch (ClassNotFoundException e) { throw new AssertionError("DTLS handshake allocation guard is missing"); }
        var method = guard.getDeclaredMethod("validDatagram", byte[].class, int.class, int.class);
        method.setAccessible(true);
        return (boolean) method.invoke(null, packet, 0, packet.length);
    }
    static byte[] fragment(int declared, int offset, int actual) {
        byte[] bytes = new byte[13 + 12 + actual];
        bytes[0] = 22;
        bytes[1] = (byte) 254;
        bytes[2] = (byte) 253;
        bytes[11] = (byte) ((12 + actual) >>> 8);
        bytes[12] = (byte) (12 + actual);
        bytes[13] = 2;
        uint24(bytes, 14, declared);
        uint24(bytes, 19, offset);
        uint24(bytes, 22, actual);
        return bytes;
    }
    static void uint24(byte[] bytes, int index, int value) {
        bytes[index] = (byte) (value >>> 16);
        bytes[index + 1] = (byte) (value >>> 8);
        bytes[index + 2] = (byte) value;
    }
    public static void main(String[] args) throws Exception {
        ValidationTests.check(valid(fragment(4096, 0, 1)), "Bounded fragments must be accepted");
        ValidationTests.check(valid(fragment(0, 0, 0)), "Empty handshake body must be accepted");
        ValidationTests.check(!valid(fragment(4097, 0, 1)), "Oversized declared handshake must be rejected before allocation");
        ValidationTests.check(!valid(fragment(0xffffff, 0, 1)), "Maximum attacker-declared handshake must be rejected");
        ValidationTests.check(!valid(fragment(16, 16, 1)), "Out-of-range fragment must be rejected");
        ValidationTests.check(!valid(Arrays.copyOf(fragment(1, 0, 1), 24)), "Truncated datagram must be rejected");
        ValidationTests.check(!valid(new byte[0]), "Empty datagram must be rejected");
        byte[] epochOne = fragment(0xffffff, 0, 1);
        epochOne[4] = 1;
        ValidationTests.check(valid(epochOne), "Encrypted epoch payload must be delegated to authenticated DTLS decoding");
        byte[] joined = new byte[52];
        System.arraycopy(fragment(1, 0, 1), 0, joined, 0, 26);
        System.arraycopy(fragment(4097, 0, 1), 0, joined, 26, 26);
        ValidationTests.check(!valid(joined), "Every record in the datagram must be checked");
        System.out.println("DTLS DATAGRAM GUARD TESTS PASS: 9");
    }
}
