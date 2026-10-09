package fr.theo.hueserver;

import java.io.IOException;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.Arrays;
import org.bouncycastle.tls.CipherSuite;
import org.bouncycastle.tls.DTLSClientProtocol;
import org.bouncycastle.tls.DTLSTransport;
import org.bouncycastle.tls.PSKTlsClient;
import org.bouncycastle.tls.ProtocolVersion;
import org.bouncycastle.tls.TlsPSKIdentity;

import org.bouncycastle.tls.crypto.impl.bc.BcTlsCrypto;

/** Connected UDP DTLS with no certificate, cipher or protocol fallback. */
final class DtlsPskTransport implements StreamMain.PacketSink {
    private final DatagramSocket socket;
    private final DTLSTransport dtls;
    private DtlsPskTransport(DatagramSocket socket, DTLSTransport dtls) {
        this.socket = socket;
        this.dtls = dtls;
    }

    static DtlsPskTransport open(Validation.Config config) throws IOException {
        // Raw address construction performs no DNS lookup and rejects non-RFC1918 IPv4.
        InetAddress address = InetAddress.getByAddress(Validation.privateAddress(config.ip));
        DatagramSocket socket = new DatagramSocket();
        try {
            socket.connect(address, 2100);
            return connect(config, socket);
        } catch (IOException | RuntimeException e) {
            socket.close();
            throw e;
        }
    }

    /** Internal connected-socket seam for local-only integration tests; not a CLI option. */
    static DtlsPskTransport connect(Validation.Config config, DatagramSocket socket) throws IOException {
        byte[] identity = config.username.getBytes(StandardCharsets.UTF_8);
        byte[] key = config.clientkey.clone();
        TlsPSKIdentity psk = new TlsPSKIdentity() {
            @Override public void skipIdentityHint() { }
            @Override public void notifyIdentityHint(byte[] hint) { }
            @Override public byte[] getPSKIdentity() { return identity.clone(); }
            @Override public byte[] getPSK() { return key.clone(); }
        };
        PSKTlsClient client = new PSKTlsClient(new BcTlsCrypto(new SecureRandom()), psk) {
            @Override protected ProtocolVersion[] getSupportedVersions() { return ProtocolVersion.DTLSv12.only(); }
            @Override protected int[] getSupportedCipherSuites() { return new int[] {CipherSuite.TLS_PSK_WITH_AES_128_GCM_SHA256}; }
            @Override public int getHandshakeTimeoutMillis() { return 10000; }
        };
        try {
            if (!socket.isConnected()) throw new IOException("handshake-failed");
            return new DtlsPskTransport(socket, new DTLSClientProtocol().connect(client, new BoundedUdpTransport(socket)));
        } catch (IOException | RuntimeException e) {
            socket.close();
            throw e;
        } finally {
            Arrays.fill(identity, (byte) 0);
            Arrays.fill(key, (byte) 0);
        }
    }

    @Override public void send(byte[] packet) throws IOException {
        dtls.send(packet, 0, packet.length);
    }
    @Override public void close() throws IOException {
        try { dtls.close(); }
        finally { socket.close(); }
    }
}
