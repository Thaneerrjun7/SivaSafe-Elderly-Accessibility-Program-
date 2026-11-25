// SivaSafe desktop launcher.
//
// Double-clicking this (or its desktop shortcut) starts SivaSafe exactly the
// way `python main.py` does — same interpreter, same working directory — but
// with no IDE, no terminal, and no console window.
//
// It deliberately does NOT bundle Python or the app. It finds the project's
// own virtual environment and runs main.py out of the source tree, so every
// edit to the code is live the next time you click it; there is nothing to
// rebuild. The only reason to rebuild this file is to change the launcher
// itself (see build.ps1).
//
// Three things it does beyond spawning the process:
//   1. Refuses to start a second copy. Two instances fight over port 8000 and
//      the loser silently loses Remote Control for the whole session.
//   2. Keeps a log. pythonw.exe has nowhere to print, so its output is piped
//      to logs\launcher.log — that is where a traceback ends up.
//   3. Reports a crash. If the app dies with a failure code the user gets a
//      dialog with the tail of that log, instead of a click that appears to
//      do nothing at all.
//
// Because of (2) and (3) this process stays alive for as long as SivaSafe
// does, draining the pipes. It is windowless and costs a few megabytes.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Management;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

internal static class SivaSafeLauncher
{
    private const string Title = "SivaSafe";

    // How much of the log a crash dialog shows.
    private const int TailLines = 24;

    private static StreamWriter _log;
    private static readonly object _logLock = new object();
    private static readonly Queue<string> _tail = new Queue<string>();

    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            return Run(args);
        }
        catch (Exception ex)
        {
            Complain("SivaSafe could not be started.\r\n\r\n" + ex.Message);
            return 1;
        }
        finally
        {
            CloseLog();
        }
    }

    private static int Run(string[] args)
    {
        // --debug swaps pythonw.exe for python.exe and lets the console show,
        // so output is watched live instead of read back out of the log.
        bool debug = false;
        foreach (string a in args)
        {
            string flag = a.ToLowerInvariant();
            if (flag == "--debug" || flag == "-d") debug = true;
        }

        string exeDir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);

        string projectDir = FindProjectDir(exeDir);
        if (projectDir == null)
        {
            Complain(
                "Could not find main.py.\r\n\r\n" +
                "SivaSafe.exe has to sit in the same folder as main.py " +
                "(or in the folder just above it).\r\n\r\n" +
                "Looked from: " + exeDir);
            return 1;
        }
        string mainPy = Path.Combine(projectDir, "main.py");

        if (AlreadyRunning(projectDir, mainPy))
        {
            MessageBox.Show(
                "SivaSafe is already running.\r\n\r\n" +
                "Look for its window, or check the taskbar. Starting a second " +
                "copy would break Remote Control for both of them.",
                Title, MessageBoxButtons.OK, MessageBoxIcon.Information);
            return 0;
        }

        string python = FindPython(projectDir, debug);
        if (python == null)
        {
            Complain(
                "Could not find Python.\r\n\r\n" +
                "SivaSafe expects the project's virtual environment at:\r\n" +
                Path.Combine(Path.GetDirectoryName(projectDir) ?? projectDir,
                             @".venv\Scripts\pythonw.exe") + "\r\n\r\n" +
                "If the environment moved, recreate it there, or install Python " +
                "so that it is on the system PATH.");
            return 1;
        }

        if (!debug) OpenLog(projectDir, python, mainPy);

        ProcessStartInfo psi = new ProcessStartInfo(python, Quote(mainPy));
        psi.WorkingDirectory = projectDir;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = !debug;
        // main.py sets both of these itself, but only after its imports have
        // run — an import that dies early would print mojibake, or nothing.
        psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        psi.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
        if (!debug)
        {
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            // Without this the pipes are read in the console codepage and every
            // ✓ and ⚠ the app prints lands in the log as mojibake.
            psi.StandardOutputEncoding = new UTF8Encoding(false);
            psi.StandardErrorEncoding = new UTF8Encoding(false);
        }

        Process app;
        try
        {
            app = Process.Start(psi);
        }
        catch (Exception ex)
        {
            Complain("SivaSafe could not be started.\r\n\r\n" +
                     python + "\r\n\r\n" + ex.Message);
            return 1;
        }

        if (!debug)
        {
            app.OutputDataReceived += OnOutput;
            app.ErrorDataReceived += OnOutput;
            app.BeginOutputReadLine();
            app.BeginErrorReadLine();
        }

        app.WaitForExit();
        int code = app.ExitCode;
        CloseLog();

        // Exit code 0 covers both a normal quit and a cancelled sign-in, which
        // main.py exits cleanly. Anything else is a genuine failure and the
        // user has, so far, seen nothing at all.
        if (code != 0 && !debug)
        {
            Complain(
                "SivaSafe stopped unexpectedly (exit code " + code + ").\r\n\r\n" +
                Tail() + "\r\n\r\n" +
                "Full log: " + Path.Combine(projectDir, @"logs\launcher.log"));
        }
        return code;
    }

    // ── Locating things ──────────────────────────────────────────────────

    /// <summary>Nearest folder holding main.py, searching from the exe out.</summary>
    private static string FindProjectDir(string exeDir)
    {
        List<string> candidates = new List<string>();
        candidates.Add(exeDir);
        candidates.Add(Path.Combine(exeDir, "Mark-XLVI-main"));
        string parent = Path.GetDirectoryName(exeDir);
        if (parent != null)
        {
            candidates.Add(parent);
            candidates.Add(Path.Combine(parent, "Mark-XLVI-main"));
        }

        foreach (string dir in candidates)
        {
            if (dir != null && File.Exists(Path.Combine(dir, "main.py")))
                return Path.GetFullPath(dir);
        }
        return null;
    }

    /// <summary>
    /// The interpreter to run main.py with: the project's own virtual
    /// environment if there is one, otherwise whatever is on PATH.
    /// </summary>
    private static string FindPython(string projectDir, bool debug)
    {
        string exe = debug ? "python.exe" : "pythonw.exe";
        string[] envNames = { ".venv", "venv", "env" };

        // The venv lives beside the project or one level up, depending on how
        // the tree was unpacked; walking up covers both without guessing.
        string dir = projectDir;
        for (int depth = 0; depth < 4 && dir != null; depth++)
        {
            foreach (string name in envNames)
            {
                string candidate = Path.Combine(dir, name + @"\Scripts\" + exe);
                if (File.Exists(candidate)) return candidate;
            }
            dir = Path.GetDirectoryName(dir);
        }

        string path = Environment.GetEnvironmentVariable("PATH") ?? "";
        foreach (string entry in path.Split(';'))
        {
            if (entry.Length == 0) continue;
            try
            {
                string candidate = Path.Combine(entry.Trim('"'), exe);
                if (File.Exists(candidate)) return candidate;
            }
            catch (ArgumentException)
            {
                // A malformed PATH entry is not worth failing the launch over.
            }
        }
        return null;
    }

    /// <summary>Is a copy of this same main.py already running?</summary>
    private static bool AlreadyRunning(string projectDir, string mainPy)
    {
        try
        {
            string query =
                "SELECT ProcessId, CommandLine FROM Win32_Process " +
                "WHERE Name = 'pythonw.exe' OR Name = 'python.exe'";
            using (ManagementObjectSearcher search = new ManagementObjectSearcher(query))
            using (ManagementObjectCollection found = search.Get())
            {
                foreach (ManagementObject proc in found)
                {
                    object raw = proc["CommandLine"];
                    if (raw == null) continue;
                    string cmd = raw.ToString();

                    // Started from a shortcut (absolute path), or from a shell
                    // sitting in the project folder (bare `main.py`) — the IDE
                    // run this launcher is meant to replace does the latter.
                    bool sameApp =
                        cmd.IndexOf(mainPy, StringComparison.OrdinalIgnoreCase) >= 0 ||
                        (cmd.IndexOf("main.py", StringComparison.OrdinalIgnoreCase) >= 0 &&
                         cmd.IndexOf(projectDir, StringComparison.OrdinalIgnoreCase) >= 0);
                    if (sameApp) return true;
                }
            }
        }
        catch (Exception)
        {
            // WMI can be disabled or slow to answer. Being unable to check is
            // not a reason to refuse to start.
        }
        return false;
    }

    // ── Log ──────────────────────────────────────────────────────────────

    private static void OpenLog(string projectDir, string python, string mainPy)
    {
        try
        {
            string logDir = Path.Combine(projectDir, "logs");
            Directory.CreateDirectory(logDir);
            string current = Path.Combine(logDir, "launcher.log");

            // One run back is kept: a crash is usually reported after the
            // window has already been clicked again.
            if (File.Exists(current))
            {
                string previous = Path.Combine(logDir, "launcher-previous.log");
                try
                {
                    if (File.Exists(previous)) File.Delete(previous);
                    File.Move(current, previous);
                }
                catch (IOException) { /* fall through and append instead */ }
            }

            _log = new StreamWriter(current, true, new UTF8Encoding(false));
            _log.AutoFlush = true;
            _log.WriteLine("=== SivaSafe launched " +
                           DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " ===");
            _log.WriteLine("python : " + python);
            _log.WriteLine("script : " + mainPy);
            _log.WriteLine();
        }
        catch (Exception)
        {
            _log = null;     // an unwritable folder must not stop the app
        }
    }

    private static void OnOutput(object sender, DataReceivedEventArgs e)
    {
        if (e.Data == null) return;
        lock (_logLock)
        {
            if (_log != null)
            {
                try { _log.WriteLine(e.Data); }
                catch (Exception) { _log = null; }
            }
            _tail.Enqueue(e.Data);
            while (_tail.Count > TailLines) _tail.Dequeue();
        }
    }

    private static string Tail()
    {
        lock (_logLock)
        {
            if (_tail.Count == 0) return "It did not report a reason.";
            return string.Join("\r\n", _tail.ToArray());
        }
    }

    private static void CloseLog()
    {
        lock (_logLock)
        {
            if (_log == null) return;
            try { _log.Dispose(); }
            catch (Exception) { }
            _log = null;
        }
    }

    // ── Misc ─────────────────────────────────────────────────────────────

    private static string Quote(string path)
    {
        return "\"" + path + "\"";
    }

    private static void Complain(string message)
    {
        MessageBox.Show(message, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
