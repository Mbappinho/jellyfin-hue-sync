package fr.theo.hueserver;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.io.Reader;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;

/** stdin-only credentials, READY-only stdout, generic-only stderr diagnostics. */
public final class StreamMain {
    private StreamMain() { }

    interface PacketSink extends AutoCloseable {
        void send(byte[] packet) throws IOException;
        @Override void close() throws IOException;
    }
    @FunctionalInterface interface Connector {
        PacketSink connect(Validation.Config config) throws IOException;
    }

    public static void main(String[] args) {
        if (args.length != 0) {
            System.err.println("invalid-arguments");
            System.exit(2);
        }
        int code;
        try { code = run(System.in, System.out, System.err, DtlsPskTransport::open); }
        catch (LinkageError e) { System.err.println("runtime-unavailable"); code = 7; }
        if (code != 0) System.exit(code);
    }

    static int run(InputStream input, PrintStream output, PrintStream error, Connector connector) {
        String label = "invalid-config";
        int code = 2;
        var decoder = StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT);
        try (Reader reader = new BufferedReader(new InputStreamReader(input, decoder), 4096);
             Validation.Config config = Validation.parseConfig(line(reader, 4096))) {
            label = "handshake-failed";
            code = 3;
            try (PacketSink transport = connector.connect(config)) {
                // The original PSK is no longer needed after the handshake completes.
                config.wipe();
                output.println("READY");
                output.flush();
                if (output.checkError()) throw new IOException("output-failed");
                while (true) {
                    label = "invalid-packet";
                    code = 4;
                    String encoded = line(reader, 212);
                    if (encoded == null) break;
                    byte[] packet = Validation.parsePacket(encoded);
                    label = "stream-failed";
                    code = 6;
                    transport.send(packet);
                }
                label = "stream-failed";
                code = 6;
            }
            return 0;
        } catch (Exception e) {
            // Never print exception messages, causes, input, identities or private addresses.
            error.println(label);
            return code;
        }
    }

    /** Length bounded before materializing a line; accepts LF, CRLF and final unterminated line. */
    static String line(Reader reader, int maximum) throws IOException {
        StringBuilder line = new StringBuilder(Math.min(maximum, 256));
        int next;
        while ((next = reader.read()) != -1 && next != '\n') {
            if (line.length() >= maximum + 1) throw new IOException("input-limit");
            line.append((char) next);
        }
        if (next == -1 && line.isEmpty()) return null;
        if (!line.isEmpty() && line.charAt(line.length() - 1) == '\r') line.setLength(line.length() - 1);
        if (line.length() > maximum) throw new IOException("input-limit");
        return line.toString();
    }
}
