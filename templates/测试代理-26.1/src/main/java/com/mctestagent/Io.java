package com.mctestagent;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.List;

/** 文件与 JSON 的小工具。代理不联网、不开端口，全部靠文件通信。 */
final class Io {
    static final Gson GSON = new GsonBuilder().disableHtmlEscaping().serializeNulls().create();

    /** 工作目录：相对游戏运行目录（runClient 的工作目录就是 run/）。 */
    static final Path DIR = Paths.get("测试代理");
    static final Path INBOX = DIR.resolve("inbox.jsonl");
    static final Path OUTBOX = DIR.resolve("outbox.jsonl");
    static final Path STATUS = DIR.resolve("状态.json");
    static final Path READY = DIR.resolve("就绪.flag");
    static final Path BOOT = DIR.resolve("启动.json");
    static final Path SHOTS = DIR.resolve("截图");

    private Io() {
    }

    static void ensureDirs() {
        try {
            Files.createDirectories(SHOTS);
        } catch (IOException e) {
            warn("建工作目录失败：" + e);
        }
    }

    static synchronized void appendLine(Path p, String line) {
        try {
            Files.writeString(p, line + "\n", StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND, StandardOpenOption.WRITE);
        } catch (IOException e) {
            warn("追加 " + p + " 失败：" + e);
        }
    }

    static synchronized void writeText(Path p, String text) {
        try {
            Files.writeString(p, text, StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING, StandardOpenOption.WRITE);
        } catch (IOException e) {
            warn("写入 " + p + " 失败：" + e);
        }
    }

    static List<String> readAllLines(Path p) {
        try {
            if (!Files.exists(p)) return List.of();
            return new ArrayList<>(Files.readAllLines(p, StandardCharsets.UTF_8));
        } catch (IOException e) {
            return List.of();
        }
    }

    static JsonObject readJson(Path p) {
        try {
            if (!Files.exists(p)) return null;
            return JsonParser.parseString(Files.readString(p, StandardCharsets.UTF_8)).getAsJsonObject();
        } catch (Exception e) {
            warn("解析 " + p + " 失败：" + e);
            return null;
        }
    }

    /**
     * 代理的诊断输出。
     *
     * <p>⚠️ 为什么还要写文件：Gradle 抓子进程 stdout 时用的编码和 JVM 的默认编码（zh-CN 上是 GBK）
     * 对不上，中文在构建日志里会变成乱码（实测：{@code [mctestagent] ���Դ����Ѽ���}）。
     * JSONL 产物本身是显式 UTF-8 写的、没这个问题；但人读的诊断信息得落到文件里才不瞎。
     */
    static void warn(String msg) {
        System.out.println("[mctestagent] " + msg);
        try {
            Files.writeString(DIR.resolve("代理.log"), "[mctestagent] " + msg + "\n", StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND, StandardOpenOption.WRITE);
        } catch (Throwable ignored) {
        }
    }
}
