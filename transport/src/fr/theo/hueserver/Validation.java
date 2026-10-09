package fr.theo.hueserver;

import java.util.HashMap;
import java.util.Map;
import java.util.Arrays;

/** Standalone input validation; never includes supplied values in exceptions. */
final class Validation {
    private Validation() { }

    static final class Config implements AutoCloseable {
        final String ip;
        final String username;
        final byte[] clientkey;
        Config(String ip, String username, String clientkey) {
            this.ip = ip;
            this.username = username;
            this.clientkey = new byte[16];
            for (int i = 0; i < 16; i++) this.clientkey[i] = (byte) ((hex(clientkey.charAt(i * 2)) << 4) | hex(clientkey.charAt(i * 2 + 1)));
        }
        void wipe() { Arrays.fill(clientkey, (byte) 0); }
        @Override public void close() { wipe(); }
    }

    static Config parseConfig(String line) {
        Map<String, String> fields = new Json(line).object();
        if (fields.size() != 3) throw invalid();
        String ip = fields.get("ip");
        privateAddress(ip);
        if (!fields.get("username").matches("[A-Za-z0-9_-]{1,128}")) throw invalid();
        if (!fields.get("clientkey").matches("[a-fA-F0-9]{32}")) throw invalid();
        return new Config(fields.get("ip"), fields.get("username"), fields.get("clientkey"));
    }

    static byte[] privateAddress(String ip) {
        if (ip == null || !ip.matches("(?:0|[1-9][0-9]{0,2})(?:\\.(?:0|[1-9][0-9]{0,2})){3}")) throw invalid();
        String[] parts = ip.split("\\.");
        int[] values = new int[4];
        byte[] bytes = new byte[4];
        for (int i = 0; i < 4; i++) {
            values[i] = Integer.parseInt(parts[i]);
            if (values[i] > 255) throw invalid();
            bytes[i] = (byte) values[i];
        }
        if (!(values[0] == 10 || (values[0] == 172 && values[1] >= 16 && values[1] <= 31)
                || (values[0] == 192 && values[1] == 168))) throw invalid();
        return bytes;
    }

    static byte[] parsePacket(String line) {
        if (line == null || line.length() < 50 || line.length() > 212 || (line.length() & 1) != 0) throw invalid();
        byte[] packet = new byte[line.length() / 2];
        for (int i = 0; i < packet.length; i++) packet[i] = (byte) ((hex(line.charAt(i * 2)) << 4) | hex(line.charAt(i * 2 + 1)));
        if ((packet.length - 16) % 9 != 0) throw invalid();
        byte[] header = {72,117,101,83,116,114,101,97,109,1,0,0,0,0,0,0};
        for (int i = 0; i < 16; i++) if (i != 11 && packet[i] != header[i]) throw invalid();
        boolean[] seen = new boolean[256];
        for (int offset = 16; offset < packet.length; offset += 9) {
            int id = packet[offset + 2] & 255;
            if (packet[offset] != 0 || packet[offset + 1] != 0 || id == 0 || seen[id]) throw invalid();
            seen[id] = true;
            // Each RGB component occupies exactly two bytes: all uint16 values are valid.
        }
        return packet;
    }

    private static int hex(char c) {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        throw invalid();
    }

    private static final class Json {
        private final String input;
        private int index;
        Json(String input) {
            if (input == null || input.length() > 4096) throw invalid();
            this.input = input;
        }
        Map<String, String> object() {
            Map<String, String> values = new HashMap<>();
            expect('{');
            whitespace();
            if (!take('}')) {
                do {
                    String name = string();
                    if (!name.equals("ip") && !name.equals("username") && !name.equals("clientkey")) throw invalid();
                    if (values.containsKey(name)) throw invalid();
                    expect(':');
                    values.put(name, string());
                    whitespace();
                    if (take('}')) break;
                    expect(',');
                } while (true);
            }
            whitespace();
            if (index != input.length()) throw invalid();
            return values;
        }
        String string() {
            expect('"');
            StringBuilder value = new StringBuilder();
            while (index < input.length()) {
                char c = input.charAt(index++);
                if (c == '"') return value.toString();
                if (c < 0x20) throw invalid();
                if (c != '\\') { value.append(c); continue; }
                if (index == input.length()) throw invalid();
                char escaped = input.charAt(index++);
                switch (escaped) {
                    case '"', '\\', '/' -> value.append(escaped);
                    case 'b' -> value.append('\b');
                    case 'f' -> value.append('\f');
                    case 'n' -> value.append('\n');
                    case 'r' -> value.append('\r');
                    case 't' -> value.append('\t');
                    case 'u' -> {
                        if (index + 4 > input.length()) throw invalid();
                        int code = 0;
                        for (int i = 0; i < 4; i++) {
                            int digit = hex(input.charAt(index++));
                            code = code * 16 + digit;
                        }
                        value.append((char) code);
                    }
                    default -> throw invalid();
                }
            }
            throw invalid();
        }
        void whitespace() {
            while (index < input.length() && " \t\r\n".indexOf(input.charAt(index)) >= 0) index++;
        }
        void expect(char expected) {
            whitespace();
            if (!take(expected)) throw invalid();
        }
        boolean take(char expected) {
            if (index < input.length() && input.charAt(index) == expected) { index++; return true; }
            return false;
        }
    }
    private static IllegalArgumentException invalid() { return new IllegalArgumentException("invalid-input"); }
}
