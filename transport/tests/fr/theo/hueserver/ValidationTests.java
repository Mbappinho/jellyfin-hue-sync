package fr.theo.hueserver;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;

/** Fixtures are deliberately artificial and never printed. */
public final class ValidationTests {
    private static int passed;
    private static final String FIXTURE = " { \"ip\" : \"192.168.7.9\", \"username\":\"artificial_test_identity\", \"clientkey\":\"00112233445566778899aabbccddeeff\" } ";
    private ValidationTests() { }

    static Object call(String method, Class<?> argumentType, Object argument) throws Exception {
        Class<?> validation;
        try { validation = Class.forName("fr.theo.hueserver.Validation"); }
        catch (ClassNotFoundException e) { throw new AssertionError("Validation feature is missing"); }
        Method target;
        try { target = validation.getDeclaredMethod(method, argumentType); }
        catch (NoSuchMethodException e) { throw new AssertionError("Requested validation feature is missing"); }
        target.setAccessible(true);
        try { return target.invoke(null, argument); }
        catch (InvocationTargetException e) {
            if (e.getCause() instanceof Exception cause) throw cause;
            throw new AssertionError("Unexpected test failure");
        }
    }

    static void check(boolean result, String label) {
        if (!result) throw new AssertionError(label);
    }
    static void test(String name, CheckedRunnable body) throws Exception {
        body.run();
        passed++;
        System.out.println("PASS " + name);
    }
    @FunctionalInterface interface CheckedRunnable { void run() throws Exception; }

    public static void main(String[] args) throws Exception {
        test("valid whitespace-delimited JSON configuration", () -> {
            Object config = call("parseConfig", String.class, FIXTURE);
            check(config != null, "Valid configuration must be accepted");
            var ip = config.getClass().getDeclaredField("ip");
            ip.setAccessible(true);
            check("192.168.7.9".equals(ip.get(config)), "Parsed IP must match fixture");
            var username = config.getClass().getDeclaredField("username");
            username.setAccessible(true);
            check("artificial_test_identity".equals(username.get(config)), "Parsed identity must match fixture");
        });
        test("reject unknown duplicate and missing JSON fields", () -> {
            for (String input : new String[] {
                    FIXTURE.replace("\"ip\" :", "\"ip\":\"10.1.2.3\", \"ip\" :"),
                    FIXTURE.replace("\"ip\" :", "\"\\u0069p\":\"10.1.2.3\", \"ip\" :"),
                    FIXTURE.replace("\"ip\" :", "\"extra\":\"x\", \"ip\" :"),
                    FIXTURE.replace("\"ip\"", "\"IP\""), "{}",
                    FIXTURE.replace("\"username\":\"artificial_test_identity\",", "") }) {
                boolean rejected = false;
                try { call("parseConfig", String.class, input); }
                catch (IllegalArgumentException expected) { rejected = true; }
                check(rejected, "Invalid JSON schema must be rejected");
            }
        });
        test("enforce private IPv4 and credential value constraints", () -> {
            for (String address : new String[] {"10.0.0.1", "172.16.0.1", "172.31.255.254", "192.168.0.1"})
                call("parseConfig", String.class, FIXTURE.replace("192.168.7.9", address));
            for (String address : new String[] {"127.0.0.1", "8.8.8.8", "172.15.0.1", "172.32.0.1", "192.169.0.1", "10.256.0.1", "010.0.0.1", "localhost", "::1", "10.1.2", "10.1.2.3.example", "10.1.2.3 ", ""})
                badConfig(FIXTURE.replace("192.168.7.9", address));
            for (String identity : new String[] {"", "bad identity", "bad\\nidentity", "bad\\u0000identity", "a".repeat(129)})
                badConfig(FIXTURE.replace("artificial_test_identity", identity));
            for (String key : new String[] {"", "0".repeat(31), "0".repeat(33), "g".repeat(32), "ff ".repeat(16)})
                badConfig(FIXTURE.replace("00112233445566778899aabbccddeeff", key));
            badConfig(" ".repeat(4097) + FIXTURE);
            badConfig(FIXTURE.replace("\"ip\"", "\"\\u００６９p\""));
        });
        test("decode complete one-to-ten lamp HueStream v1 RGB packets", () -> {
            for (int lights = 1; lights <= 10; lights++) {
                byte[] original = packet(lights);
                String line = java.util.HexFormat.of().formatHex(original);
                byte[] decoded = (byte[]) call("parsePacket", String.class, line);
                check(java.util.Arrays.equals(original, decoded), "Packet bytes must be preserved");
                decoded = (byte[]) call("parsePacket", String.class, line.toUpperCase(java.util.Locale.ROOT));
                check(java.util.Arrays.equals(original, decoded), "Uppercase hex must be supported");
            }
        });
        test("reject malformed HueStream header and packet framing", () -> {
            for (int offset : new int[] {0,1,2,3,4,5,6,7,8,9,10,12,13,14,15}) {
                byte[] invalid = packet(1);
                invalid[offset] ^= 1;
                badPacket(java.util.HexFormat.of().formatHex(invalid));
            }
            String valid = java.util.HexFormat.of().formatHex(packet(1));
            for (String input : new String[] {"", "00".repeat(16), valid.substring(1), valid + "00", "00".repeat(107), valid + " ", " " + valid, "z" + valid.substring(1), "０" + valid.substring(1)})
                badPacket(input);
        });
        test("reject invalid lamp ids types and duplicates", () -> {
            for (int offset : new int[] {16,17,18}) {
                byte[] invalid = packet(1);
                invalid[offset] = (byte) (offset == 18 ? 0 : 1);
                badPacket(java.util.HexFormat.of().formatHex(invalid));
            }
            byte[] duplicates = packet(2);
            duplicates[27] = duplicates[18];
            badPacket(java.util.HexFormat.of().formatHex(duplicates));
            byte[] rgb16 = packet(1);
            rgb16[19] = 0x12;
            rgb16[20] = 0x34;
            check(java.util.Arrays.equals(rgb16, (byte[]) call("parsePacket", String.class, java.util.HexFormat.of().formatHex(rgb16))), "Unsigned 16-bit RGB must be preserved");
        });
        test("reject malformed JSON syntax and non-string values", () -> {
            for (String input : new String[] {null, "[]", FIXTURE + "true", "\\ufeff" + FIXTURE,
                    FIXTURE.replace(" } ", ", } "), FIXTURE.replace("\"192.168.7.9\"", "null"),
                    FIXTURE.replace("\"192.168.7.9\"", "12"), FIXTURE.replace("\"192.168.7.9\"", "{}"),
                    FIXTURE.replace("\"192.168.7.9\"", "[]"), FIXTURE.replace("\"ip\" :", "\"ip\""),
                    FIXTURE.substring(0, FIXTURE.length() - 3), FIXTURE.replace("\"ip\"", "'ip'"),
                    FIXTURE.replace("artificial_test_identity", "bad\\qidentity"),
                    FIXTURE.replace("artificial_test_identity", "bad\\u001identity"),
                    FIXTURE.replace("artificial_test_identity", "bad\tidentity") }) badConfig(input);
            call("parseConfig", String.class, FIXTURE.replace("\"ip\"", "\"\\u0069p\""));
            call("parseConfig", String.class, FIXTURE.replace("artificial_test_identity", "artificial\\u005ftest_identity"));
        });
        test("decode artificial PSK and wipe mutable key storage", () -> {
            try (var config = Validation.parseConfig(FIXTURE)) {
                check(java.util.Arrays.equals(config.clientkey, java.util.HexFormat.of().parseHex("00112233445566778899aabbccddeeff")), "PSK decoding mismatch");
                check(!config.toString().contains("artificial_test_identity"), "Configuration string must not expose identity");
                config.wipe();
                check(java.util.Arrays.equals(config.clientkey, new byte[16]), "Mutable key storage must be wiped");
            }
        });
        System.out.println("VALIDATION TESTS PASS: " + passed);
    }
    static void badConfig(String input) throws Exception {
        boolean rejected = false;
        try { call("parseConfig", String.class, input); }
        catch (IllegalArgumentException expected) { rejected = true; }
        check(rejected, "Invalid configuration values must be rejected");
    }
    static void badPacket(String input) throws Exception {
        boolean rejected = false;
        try { call("parsePacket", String.class, input); }
        catch (IllegalArgumentException expected) { rejected = true; }
        check(rejected, "Invalid packet must be rejected");
    }
    static byte[] packet(int lamps) {
        byte[] packet = new byte[16 + lamps * 9];
        byte[] header = {72,117,101,83,116,114,101,97,109,1,0,(byte)255,0,0,0,0};
        System.arraycopy(header, 0, packet, 0, 16);
        for (int i = 0; i < lamps; i++) {
            int offset = 16 + 9 * i;
            packet[offset + 2] = (byte) (i == lamps - 1 ? 255 : i + 1);
            for (int c = 0; c < 3; c++) {
                packet[offset + 3 + c * 2] = (byte) (c * 127);
                packet[offset + 4 + c * 2] = (byte) (c * 127);
            }
        }
        return packet;
    }
}
