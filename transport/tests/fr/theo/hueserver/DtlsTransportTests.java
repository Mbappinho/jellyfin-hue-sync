package fr.theo.hueserver;

import java.io.IOException;
import java.lang.reflect.InvocationTargetException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.net.SocketException;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.Arrays;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.bouncycastle.tls.CipherSuite;
import org.bouncycastle.tls.DatagramTransport;
import org.bouncycastle.tls.DTLSServerProtocol;
import org.bouncycastle.tls.DTLSTransport;
import org.bouncycastle.tls.PSKTlsServer;
import org.bouncycastle.tls.ProtocolVersion;
import org.bouncycastle.tls.TlsPSKIdentityManager;
import org.bouncycastle.tls.crypto.impl.bc.BcTlsCrypto;

/** Local-only interoperability; the artificial identity and key are never logged. */
public final class DtlsTransportTests {
    private DtlsTransportTests() { }
    static final String CONFIG = "{\"ip\":\"192.168.7.9\",\"username\":\"artificial_test_identity\",\"clientkey\":\"00112233445566778899aabbccddeeff\"}";

    public static void main(String[] args) throws Exception {
        Class<?> transportClass;
        try { transportClass = Class.forName("fr.theo.hueserver.DtlsPskTransport"); }
        catch (ClassNotFoundException e) { throw new AssertionError("DTLS transport feature is missing"); }
        try (var config = Validation.parseConfig(CONFIG);
             var serverSocket = new DatagramSocket(0, InetAddress.getLoopbackAddress());
             var executor = Executors.newSingleThreadExecutor()) {
            byte[][] packets = {ValidationTests.packet(1), ValidationTests.packet(10)};
            var server = executor.submit(() -> receive(serverSocket, config, packets));
            var socket = new DatagramSocket();
            socket.connect(InetAddress.getLoopbackAddress(), serverSocket.getLocalPort());
            var connect = transportClass.getDeclaredMethod("connect", Validation.Config.class, DatagramSocket.class);
            connect.setAccessible(true);
            Object transport;
            try { transport = connect.invoke(null, config, socket); }
            catch (InvocationTargetException e) { throw new AssertionError("Local DTLS handshake failed", e.getCause()); }
            var send = transportClass.getDeclaredMethod("send", byte[].class);
            send.setAccessible(true);
            try (var closeable = (AutoCloseable) transport) {
                for (byte[] packet : packets) send.invoke(closeable, (Object) packet);
            }
            ValidationTests.check(server.get(15, TimeUnit.SECONDS) == 2, "Expected two preserved datagrams");
            ValidationTests.check(socket.isClosed(), "Transport close must close UDP socket");
            System.out.println("PASS local DTLS 1.2 PSK AES-128-GCM-SHA256 handshake");
            System.out.println("PASS preserved 25-byte and 106-byte application datagrams");
            System.out.println("PASS transport closes underlying socket");
            System.out.println("DTLS INTEROPERABILITY TESTS PASS: 3 (local only; no physical bridge)");
        }
    }

    static int receive(DatagramSocket socket, Validation.Config config, byte[][] expected) throws Exception {
        byte[] identity = config.username.getBytes(StandardCharsets.UTF_8);
        PSKTlsServer server = new PSKTlsServer(new BcTlsCrypto(new SecureRandom()), new TlsPSKIdentityManager() {
            @Override public byte[] getHint() { return null; }
            @Override public byte[] getPSK(byte[] suppliedIdentity) {
                return Arrays.equals(identity, suppliedIdentity) ? config.clientkey.clone() : null;
            }
        }) {
            @Override protected ProtocolVersion[] getSupportedVersions() { return ProtocolVersion.DTLSv12.only(); }
            @Override protected int[] getSupportedCipherSuites() { return new int[] {CipherSuite.TLS_PSK_WITH_AES_128_GCM_SHA256}; }
            @Override public int getHandshakeTimeoutMillis() { return 10000; }
            @Override public void notifyHandshakeComplete() throws IOException {
                super.notifyHandshakeComplete();
                ValidationTests.check(ProtocolVersion.DTLSv12.equals(context.getServerVersion()), "Negotiated DTLS version mismatch");
                ValidationTests.check(context.getSecurityParametersConnection().getCipherSuite() == CipherSuite.TLS_PSK_WITH_AES_128_GCM_SHA256, "Negotiated cipher mismatch");
            }
        };
        DTLSTransport dtls = new DTLSServerProtocol().accept(server, new LocalServerDatagrams(socket));
        try {
            byte[] buffer = new byte[1200];
            for (byte[] packet : expected) {
                int length = dtls.receive(buffer, 0, buffer.length, 10000);
                ValidationTests.check(length == packet.length && Arrays.equals(Arrays.copyOf(buffer, length), packet), "DTLS datagram was not preserved");
            }
            int finalRead = -1;
            try { finalRead = dtls.receive(buffer, 0, buffer.length, 5000); }
            catch (SocketException e) { if (!socket.isClosed()) throw e; }
            ValidationTests.check(finalRead < 0 && socket.isClosed(), "Expected peer close_notify and no extra application datagram");
            return expected.length;
        } finally {
            dtls.close();
        }
    }

    static final class LocalServerDatagrams implements DatagramTransport {
        private final DatagramSocket socket;
        LocalServerDatagrams(DatagramSocket socket) { this.socket = socket; }
        @Override public int getReceiveLimit() { return 1200; }
        @Override public int getSendLimit() { return 1200; }
        @Override public int receive(byte[] bytes, int offset, int length, int timeout) throws IOException {
            socket.setSoTimeout(Math.max(1, timeout));
            DatagramPacket packet = new DatagramPacket(bytes, offset, length);
            try { socket.receive(packet); }
            catch (SocketTimeoutException e) { return -1; }
            if (!socket.isConnected()) socket.connect(packet.getSocketAddress());
            return packet.getLength();
        }
        @Override public void send(byte[] bytes, int offset, int length) throws IOException {
            socket.send(new DatagramPacket(bytes, offset, length));
        }
        @Override public void close() { socket.close(); }
    }
}
