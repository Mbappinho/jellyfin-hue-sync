package fr.theo.hueserver;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.net.DatagramSocket;
import java.net.DatagramPacket;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.util.HexFormat;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Exercises the complete stdin/READY/datagram/EOF flow against a real local DTLS peer. */
public final class StreamProtocolTests {
    private StreamProtocolTests() { }
    public static void main(String[] args) throws Exception {
        String first = HexFormat.of().formatHex(ValidationTests.packet(1));
        String second = HexFormat.of().formatHex(ValidationTests.packet(10));
        success(DtlsTransportTests.CONFIG + "\n" + first + "\n" + second + "\n", 2, 0, "");
        success(DtlsTransportTests.CONFIG + "\r\n" + first + "\r\n" + second, 2, 0, "");
        success(DtlsTransportTests.CONFIG + "\n", 0, 0, "");
        success(DtlsTransportTests.CONFIG + "\n" + first + "\n" + "invalid-artificial-packet\n", 1, 4, "invalid-packet\n");
        success(DtlsTransportTests.CONFIG + "\n" + "00".repeat(107) + "\n", 0, 4, "invalid-packet\n");
        wrongPsk();
        oversizedHandshake();
        silentPeerTimeout();
        System.out.println("STREAM PROTOCOL TESTS PASS: 8 (real local DTLS; no physical bridge)");
    }
    static void success(String input, int count, int expectedCode, String expectedError) throws Exception {
        try (var serverConfig = Validation.parseConfig(DtlsTransportTests.CONFIG);
             var socket = new DatagramSocket(0, InetAddress.getLoopbackAddress());
             var executor = Executors.newSingleThreadExecutor()) {
            byte[][] packets = switch (count) {
                case 0 -> new byte[0][];
                case 1 -> new byte[][] {ValidationTests.packet(1)};
                default -> new byte[][] {ValidationTests.packet(1), ValidationTests.packet(10)};
            };
            // A zero-packet server must wait for close_notify rather than close immediately.
            var future = executor.submit(() -> DtlsTransportTests.receive(socket, serverConfig, packets));
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            ByteArrayOutputStream error = new ByteArrayOutputStream();
            AtomicReference<DatagramSocket> owned = new AtomicReference<>();
            int code = StreamMain.run(new ByteArrayInputStream(input.getBytes(StandardCharsets.UTF_8)),
                    new PrintStream(output, true, StandardCharsets.UTF_8),
                    new PrintStream(error, true, StandardCharsets.UTF_8),
                    config -> connectLocal(config, socket.getLocalPort(), owned));
            ValidationTests.check(code == expectedCode, "Unexpected CLI status");
            ValidationTests.check("READY\n".equals(normalize(output)), "Stdout must contain exactly READY");
            ValidationTests.check(expectedError.equals(normalize(error)), "Stderr must contain only the generic label");
            ValidationTests.check(future.get(15, TimeUnit.SECONDS) == count, "One datagram per input line required");
            ValidationTests.check(owned.get().isClosed(), "EOF or invalid input must close UDP socket");
        }
    }
    static DtlsPskTransport connectLocal(Validation.Config config, int port, AtomicReference<DatagramSocket> owned) throws IOException {
        DatagramSocket client = new DatagramSocket();
        owned.set(client);
        client.connect(InetAddress.getLoopbackAddress(), port);
        return DtlsPskTransport.connect(config, client);
    }
    static String normalize(ByteArrayOutputStream bytes) {
        return bytes.toString(StandardCharsets.UTF_8).replace("\r\n", "\n");
    }
    static void wrongPsk() throws Exception {
        try (var serverConfig = Validation.parseConfig(DtlsTransportTests.CONFIG);
             var socket = new DatagramSocket(0, InetAddress.getLoopbackAddress());
             var executor = Executors.newSingleThreadExecutor()) {
            var server = executor.submit(() -> DtlsTransportTests.receive(socket, serverConfig, new byte[0][]));
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            ByteArrayOutputStream error = new ByteArrayOutputStream();
            AtomicReference<DatagramSocket> owned = new AtomicReference<>();
            String input = DtlsTransportTests.CONFIG.replace("00112233445566778899aabbccddeeff", "00000000000000000000000000000000") + "\n";
            int code = StreamMain.run(new ByteArrayInputStream(input.getBytes(StandardCharsets.UTF_8)),
                    new PrintStream(output, true, StandardCharsets.UTF_8), new PrintStream(error, true, StandardCharsets.UTF_8),
                    config -> connectLocal(config, socket.getLocalPort(), owned));
            ValidationTests.check(code == 3 && output.size() == 0 && "handshake-failed\n".equals(normalize(error)), "Wrong PSK must fail without READY or diagnostic leakage");
            ValidationTests.check(owned.get().isClosed(), "Failed handshake must close UDP socket");
            boolean failed = false;
            try { server.get(15, TimeUnit.SECONDS); }
            catch (ExecutionException expected) { failed = expected.getCause() instanceof IOException; }
            ValidationTests.check(failed, "Wrong PSK must not authenticate");
        }
    }
    static void oversizedHandshake() throws Exception {
        try (var socket = new DatagramSocket(0, InetAddress.getLoopbackAddress());
             var executor = Executors.newSingleThreadExecutor()) {
            var server = executor.submit(() -> {
                socket.setSoTimeout(10000);
                DatagramPacket initial = new DatagramPacket(new byte[1200], 1200);
                socket.receive(initial);
                byte[] forged = DatagramGuardTests.fragment(0xffffff, 0, 1);
                socket.send(new DatagramPacket(forged, forged.length, initial.getSocketAddress()));
                return true;
            });
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            ByteArrayOutputStream error = new ByteArrayOutputStream();
            AtomicReference<DatagramSocket> owned = new AtomicReference<>();
            int code = StreamMain.run(new ByteArrayInputStream((DtlsTransportTests.CONFIG + "\n").getBytes(StandardCharsets.UTF_8)),
                    new PrintStream(output, true, StandardCharsets.UTF_8), new PrintStream(error, true, StandardCharsets.UTF_8),
                    config -> connectLocal(config, socket.getLocalPort(), owned));
            ValidationTests.check(code == 3 && output.size() == 0 && "handshake-failed\n".equals(normalize(error)), "Oversized plaintext handshake must fail with sanitized diagnostic");
            ValidationTests.check(owned.get().isClosed() && server.get(15, TimeUnit.SECONDS), "Allocation guard must close UDP socket");
            System.out.println("PASS reject oversized unauthenticated handshake over local UDP");
        }
    }
    static void silentPeerTimeout() throws Exception {
        try (var silentSocket = new DatagramSocket(0, InetAddress.getLoopbackAddress())) {
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            ByteArrayOutputStream error = new ByteArrayOutputStream();
            AtomicReference<DatagramSocket> owned = new AtomicReference<>();
            long begin = System.nanoTime();
            int code = StreamMain.run(new ByteArrayInputStream((DtlsTransportTests.CONFIG + "\n").getBytes(StandardCharsets.UTF_8)),
                    new PrintStream(output, true, StandardCharsets.UTF_8), new PrintStream(error, true, StandardCharsets.UTF_8),
                    config -> connectLocal(config, silentSocket.getLocalPort(), owned));
            long millis = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - begin);
            ValidationTests.check(code == 3 && output.size() == 0 && "handshake-failed\n".equals(normalize(error)), "Silent peer must fail without READY");
            ValidationTests.check(millis >= 9500 && millis < 13000, "Handshake must time out at approximately 10 seconds");
            ValidationTests.check(owned.get().isClosed(), "Timed-out handshake must close UDP socket");
            System.out.println("PASS silent local peer handshake timeout: " + millis + " ms");
        }
    }
}
