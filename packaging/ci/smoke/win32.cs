// Compiled by packaging\ci\smoke.ps1 (Add-Type) for test 08: does a black console window ever show up while
// GridSim runs? A background thread looks at the visible top-level windows every 25 ms and remembers every window
// of a console class that was not there when the watch started.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace GridSimCI
{
    public static class ConsoleWatch
    {
        private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern bool IsWindowVisible(IntPtr hWnd);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetClassName(IntPtr hWnd, StringBuilder text, int max);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int max);

        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

        [DllImport("kernel32.dll")]
        private static extern uint GetConsoleCP();

        private static readonly object Gate = new object();
        private static readonly Dictionary<long, bool> Baseline = new Dictionary<long, bool>();
        private static readonly Dictionary<long, string> Seen = new Dictionary<long, string>();
        private static Thread worker;
        private static volatile bool running;

        // True when the calling process (the test script) has a console of its own, visible or not. Console
        // programs it starts then use that console and never open a window themselves.
        public static bool CallerHasConsole()
        {
            return GetConsoleCP() != 0;
        }

        private static List<KeyValuePair<long, string>> Snapshot()
        {
            List<KeyValuePair<long, string>> found = new List<KeyValuePair<long, string>>();
            EnumWindows(delegate(IntPtr hWnd, IntPtr lParam)
            {
                if (!IsWindowVisible(hWnd))
                {
                    return true;
                }
                StringBuilder cls = new StringBuilder(256);
                GetClassName(hWnd, cls, cls.Capacity);
                string name = cls.ToString();
                if (name == "ConsoleWindowClass" || name == "CASCADIA_HOSTING_WINDOW_CLASS")
                {
                    StringBuilder title = new StringBuilder(512);
                    GetWindowText(hWnd, title, title.Capacity);
                    uint processId;
                    GetWindowThreadProcessId(hWnd, out processId);
                    string owner = "?";
                    try
                    {
                        using (Process p = Process.GetProcessById((int)processId))
                        {
                            owner = p.ProcessName;
                            try
                            {
                                owner = p.MainModule.FileName;
                            }
                            catch (Exception)
                            {
                            }
                        }
                    }
                    catch (Exception)
                    {
                    }
                    found.Add(new KeyValuePair<long, string>(hWnd.ToInt64(),
                        "class=" + name + " | pid=" + processId + " | owner=" + owner + " | title=" + title.ToString()));
                }
                return true;
            }, IntPtr.Zero);
            return found;
        }

        // The console windows that are visible right now, one text per window.
        public static string[] Visible()
        {
            List<string> texts = new List<string>();
            foreach (KeyValuePair<long, string> item in Snapshot())
            {
                texts.Add(item.Value);
            }
            return texts.ToArray();
        }

        public static void Start()
        {
            lock (Gate)
            {
                if (running)
                {
                    return;
                }
                Baseline.Clear();
                Seen.Clear();
                foreach (KeyValuePair<long, string> item in Snapshot())
                {
                    Baseline[item.Key] = true;
                }
                running = true;
                worker = new Thread(Loop);
                worker.IsBackground = true;
                worker.Start();
            }
        }

        private static void Loop()
        {
            while (running)
            {
                try
                {
                    List<KeyValuePair<long, string>> now = Snapshot();
                    lock (Gate)
                    {
                        foreach (KeyValuePair<long, string> item in now)
                        {
                            if (!Baseline.ContainsKey(item.Key) && !Seen.ContainsKey(item.Key))
                            {
                                Seen[item.Key] = DateTime.Now.ToString("HH:mm:ss.fff") + " | " + item.Value;
                            }
                        }
                    }
                }
                catch (Exception)
                {
                }
                Thread.Sleep(25);
            }
        }

        // Ends the watch and returns one text per console window that appeared while it ran.
        public static string[] Stop()
        {
            running = false;
            Thread t = worker;
            if (t != null)
            {
                t.Join(3000);
            }
            lock (Gate)
            {
                List<string> texts = new List<string>(Seen.Values);
                return texts.ToArray();
            }
        }
    }
}
