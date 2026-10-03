package ssm

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsssm "github.com/aws/aws-sdk-go-v2/service/ssm"
	packersdk "github.com/hashicorp/packer-plugin-sdk/packer"
)

type cleanupHTTPClient struct {
	t      *testing.T
	called bool
}

func (c *cleanupHTTPClient) Do(req *http.Request) (*http.Response, error) {
	c.called = true
	if err := req.Context().Err(); err != nil {
		c.t.Fatalf("cleanup inherited cancellation: %v", err)
	}
	deadline, ok := req.Context().Deadline()
	if !ok || time.Until(deadline) <= 0 || time.Until(deadline) > 30*time.Second {
		c.t.Fatal("cleanup needs a fresh bounded deadline")
	}
	body, _ := io.ReadAll(req.Body)
	if !strings.Contains(string(body), "glider-test-session") {
		c.t.Fatalf("wrong session: %s", body)
	}
	return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": []string{"application/x-amz-json-1.1"}}, Body: io.NopCloser(strings.NewReader(`{"SessionId":"glider-test-session"}`))}, nil
}
func TestGliderCleanupSurvivesCanceledBuild(t *testing.T) {
	transport := &cleanupHTTPClient{t: t}
	client := awsssm.New(awsssm.Options{Region: "us-west-2", Credentials: aws.AnonymousCredentials{}, HTTPClient: transport})
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	var output strings.Builder
	ui := &packersdk.BasicUi{Writer: &output, ErrorWriter: &output}
	Session{SvcClient: client}.terminateSession(ctx, "glider-test-session", ui)
	if !transport.called {
		t.Fatal("termination request was not sent")
	}
	if output.Len() != 0 {
		t.Fatalf("unexpected cleanup error: %s", output.String())
	}
}
